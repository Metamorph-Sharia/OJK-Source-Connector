"""The pipeline, for every agency × source in config:

    discover → fetch content (paced) → check standing → deduplicate → export → alerts

Every stage records *events* (new document, metadata changed, text changed, amended,
missing from source, duplicate, …) and the run report feeds the alert rules. The
pipeline only talks to the ``Connector`` and ``AgencyProfile`` contracts, so agencies
and sources are added or replaced through config alone.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from regingest.agencies import AgencyProfile, GenericProfile, IdResolution, profile_class
from regingest.config import Config
from regingest.connectors import (
    AgencyBinding,
    Connector,
    ConnectorContext,
    PermanentSourceError,
    QuotaExhausted,
    SourceAuthError,
    SourceError,
    connector_class,
)
from regingest.dedup import DedupOutcome, find_fetched_duplicate, resolve_duplicates
from regingest.export import ExportStats, export_corpus
from regingest.models import DiscoveredDocument, DocumentContent, Origin
from regingest.monitoring import AlertManager, Notifier, build_notifiers, evaluate, severity_rank
from regingest.monitoring.summary import render_summary
from regingest.store import Store, doc_key, utcnow

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 45  # sources may date documents late (e.g. when they ingested them)
DEFAULT_MAX_READS = 25
DEFAULT_MAX_ATTEMPTS = 3
UNRESOLVED = ("conflict", "none")
MAX_LISTED = 50  # items kept per list in a report
PROGRESS_EVERY = 25  # log a progress line every N items in long stages


def _error_kind(e: SourceError) -> str:
    return "auth" if isinstance(e, SourceAuthError) else "source"


def _brief(row: Mapping[str, Any] | DiscoveredDocument, **extra: Any) -> dict[str, Any]:
    if isinstance(row, DiscoveredDocument):
        item = {"record": doc_key(row.source, row.source_id), "title": row.title}
    else:
        item = {"record": doc_key(row["source"], row["source_id"]), "canonical_id": row["canonical_id"],
                "title": row["title"]}
    return {**item, **extra}


@dataclass
class DiscoveryStats:
    agency: str
    source: str
    since: str | None
    until: str
    full: bool
    seen: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    reappeared: int = 0
    refetch_queued: int = 0
    known_before: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)
    new_unresolved: list[dict[str, Any]] = field(default_factory=list)
    fill_rates: dict[str, float] = field(default_factory=dict)
    previous_fill_rates: dict[str, float] | None = None
    error: str | None = None
    error_kind: str | None = None
    partial: bool = True  # False once the stage finished for this source


@dataclass
class FetchStats:
    source: str
    limit: int
    attempted: int = 0
    fetched: int = 0
    refetched: int = 0
    failed: int = 0
    skipped_duplicates: int = 0
    missing_primary_text: int = 0
    id_from_text: int = 0
    charged_usd: float = 0.0
    failures: list[dict[str, Any]] = field(default_factory=list)
    content_changed: list[dict[str, Any]] = field(default_factory=list)
    incomplete_texts: list[dict[str, Any]] = field(default_factory=list)
    not_instrument: list[dict[str, Any]] = field(default_factory=list)
    id_changes: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    pause_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None
    remaining: int = 0
    partial: bool = True  # False once the stage finished for this source


@dataclass
class StatusStats:
    source: str
    budget: int
    checked: int = 0
    baselined: int = 0
    failed: int = 0
    changed: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    error: str | None = None
    error_kind: str | None = None
    partial: bool = True  # False once the stage finished for this source


def _today() -> date:
    return datetime.now(UTC).date()


class Pipeline:
    def __init__(
        self,
        config: Config,
        *,
        store: Store | None = None,
        connectors: Mapping[str, Connector] | None = None,
        notifiers: list[Notifier] | None = None,
        today: Callable[[], date] = _today,
    ) -> None:
        self.config = config
        self.store = store or Store(config.data_dir)
        self._connectors: dict[str, Connector] = dict(connectors or {})
        self._profiles: dict[str, AgencyProfile] = {}
        self._notifiers = notifiers
        self._today = today
        self._run_id: int | None = None
        self._progress: dict[str, list[Any]] | None = None  # live stage stats during run()

    def close(self) -> None:
        for connector in self._connectors.values():
            connector.close()
        self.store.close()

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- plug-ins ----------------------------------------------------------------

    def context(self, source_name: str) -> ConnectorContext:
        return ConnectorContext(source_name, self.config.credentials_dir, self.config.user_agent)

    def connector(self, source_name: str) -> Connector:
        if source_name not in self._connectors:
            source = self.config.sources[source_name]
            cls = connector_class(source.connector)
            self._connectors[source_name] = cls(source.settings, self.context(source_name))
        return self._connectors[source_name]

    def profile(self, agency: str) -> AgencyProfile:
        if agency not in self._profiles:
            config = self.config.agencies.get(agency)
            cls = profile_class(config.profile) if config else GenericProfile
            self._profiles[agency] = cls(agency)
        return self._profiles[agency]

    def notifiers(self) -> list[Notifier]:
        if self._notifiers is None:
            self._notifiers = build_notifiers(list(self.config.monitoring.notify), self.config.data_dir)
        return self._notifiers

    def alert_manager(self) -> AlertManager:
        return AlertManager(self.store, self.notifiers(), renotify_hours=self.config.monitoring.renotify_hours)

    def _sources_in_use(self, only: Iterable[str] | None) -> list[str]:
        names = dict.fromkeys(s for a in self.config.agencies.values() for s in a.sources)
        wanted = set(only or ())
        return [n for n in names if not wanted or n in wanted]

    def _track(self, section: str, stats: Any) -> None:
        if self._progress is not None:
            self._progress.setdefault(section, []).append(stats)

    def _event(self, kind: str, severity: str, message: str, **kwargs: Any) -> None:
        self.store.record_event(kind, severity, message, run_id=self._run_id, **kwargs)

    # --- discover ----------------------------------------------------------------

    def _full_due(self, source: str, agency: str) -> bool:
        state = self.store.discovery_state(source, agency)
        if state is None or not state["last_full_at"]:
            return True
        age = datetime.now(UTC) - datetime.fromisoformat(state["last_full_at"])
        return age >= timedelta(days=self.config.monitoring.full_discovery_every_days)

    def discover(
        self, *, agencies: Iterable[str] | None = None, sources: Iterable[str] | None = None, full: bool | None = None
    ) -> list[DiscoveryStats]:
        """``full=None`` re-lists everything when a full discovery is due; otherwise recent windows only."""
        only_agencies = set(agencies or ())
        only_sources = set(sources or ())
        until = self._today()
        results = []
        for code, agency in self.config.agencies.items():
            if only_agencies and code not in only_agencies:
                continue
            profile = self.profile(code)
            for source_name, params in agency.sources.items():
                if only_sources and source_name not in only_sources:
                    continue
                results.append(self._discover_one(code, profile, source_name, params, until, full))
        return results

    def _discover_one(
        self, agency: str, profile: AgencyProfile, source_name: str, params: Mapping[str, Any], until: date,
        full: bool | None,
    ) -> DiscoveryStats:
        settings = self.config.sources[source_name].settings
        state = self.store.discovery_state(source_name, agency)
        is_full = state is None or (self._full_due(source_name, agency) if full is None else full)
        since = None
        if not is_full:
            since = state["last_until"] - timedelta(days=int(settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS)))
        stats = DiscoveryStats(agency, source_name, since.isoformat() if since else None, until.isoformat(), is_full)
        self._track("discovery", stats)
        stats.known_before = self.store.count_documents(source_name, agency, present_only=True)
        stats.previous_fill_rates = state["fill_rates"] if state else None

        seen_ids: list[str] = []
        filled: dict[str, int] = {}
        try:
            connector = self.connector(source_name)
            fields = ("title", "published", "official_url", *connector.monitored_fields)
            for doc in connector.discover(AgencyBinding(agency, params), since, until):
                stats.seen += 1
                seen_ids.append(doc.source_id)
                for f in fields:
                    value = getattr(doc, f) if f in ("title", "published", "official_url") else doc.metadata.get(f)
                    filled[f] = filled.get(f, 0) + (value not in (None, "", []))
                resolution = profile.resolve_id(doc)
                result = self.store.upsert_discovered(doc, resolution, content_fields=connector.content_fields)
                setattr(stats, result.outcome, getattr(stats, result.outcome) + 1)
                self._record_discovery_events(doc, resolution, result, stats, flag_unresolved=profile.resolves_ids)
        except SourceError as e:
            stats.error, stats.error_kind = f"{type(e).__name__}: {e}", _error_kind(e)
            log.error("discovery %s/%s failed: %s", agency, source_name, stats.error)
        else:
            if stats.seen:
                stats.fill_rates = {f: round(n / stats.seen, 4) for f, n in filled.items()}
            if is_full and stats.seen:
                for row in self.store.mark_missing(source_name, agency, seen_ids):
                    stats.missing.append(_brief(row))
                    self._event("missing_from_source", "warning",
                                f"{doc_key(source_name, row['source_id'])} is no longer listed by {source_name}", row=row)
            self.store.set_discovery_state(source_name, agency, until, full=is_full and stats.seen > 0,
                                           fill_rates=stats.fill_rates)
        log.info("discovered %s from %s (%s): %s seen, %s new, %s changed, %s missing",
                 agency, source_name, "full" if is_full else "incremental", stats.seen, stats.new, stats.changed,
                 len(stats.missing))
        stats.partial = False
        return stats

    def _record_discovery_events(self, doc: DiscoveredDocument, resolution: IdResolution, result: Any,
                                 stats: DiscoveryStats, *, flag_unresolved: bool) -> None:
        if result.outcome == "new":
            self._event("new_document", "info", f"new: {doc.title[:120]}", row=doc,
                        details={"canonical_id": str(resolution.id) if resolution.id else None,
                                 "published": doc.published})
            if flag_unresolved and resolution.confidence in UNRESOLVED and len(stats.new_unresolved) < MAX_LISTED:
                stats.new_unresolved.append(_brief(doc, reason=resolution.evidence))
        elif result.outcome == "changed":
            self._event("metadata_changed", "info", f"metadata changed: {', '.join(result.changed_fields)}", row=doc,
                        details={"fields": result.changed_fields})
        if result.reappeared:
            stats.reappeared += 1
            self._event("reappeared", "info", "listed by the source again", row=doc)
        if result.refetch_queued:
            stats.refetch_queued += 1
            self._event("refetch_queued", "info", "content queued for a re-read after a metadata change", row=doc)

    # --- fetch content -------------------------------------------------------------

    def fetch_content(self, *, sources: Iterable[str] | None = None, max_reads: int | None = None) -> list[FetchStats]:
        results = []
        for source_name in self._sources_in_use(sources):
            settings = self.config.sources[source_name].settings
            limit = max_reads if max_reads is not None else int(settings.get("max_content_reads_per_run", DEFAULT_MAX_READS))
            stats = FetchStats(source_name, limit)
            self._track("content", stats)
            self._fetch_source(source_name, int(settings.get("max_attempts", DEFAULT_MAX_ATTEMPTS)), stats)
            stats.remaining = self.store.count_pending(source_name)
            stats.partial = False
            log.info("content from %s: %s fetched (%s re-reads), %s failed, %s duplicates skipped, %s queued%s",
                     source_name, stats.fetched, stats.refetched, stats.failed, stats.skipped_duplicates,
                     stats.remaining, " (paused)" if stats.paused else "")
            results.append(stats)
        return results

    def _fetch_source(self, source_name: str, max_attempts: int, stats: FetchStats) -> None:
        threshold = self.config.monitoring.dedup_title_overlap
        for doc in self.store.pending_content(source_name):
            if stats.attempted >= stats.limit:
                return
            if duplicate := find_fetched_duplicate(self.store, doc, threshold):
                self.store.mark_duplicate(doc, *duplicate)
                stats.skipped_duplicates += 1
                self._event("duplicate_detected", "info", duplicate[1], row=doc)
                continue
            stats.attempted += 1
            try:
                content = self.connector(source_name).fetch_content(doc)
            except QuotaExhausted as e:
                stats.attempted -= 1
                stats.paused, stats.pause_reason = True, str(e)
                log.info("%s paused content reads: %s", source_name, e)
                return
            except SourceAuthError as e:
                stats.attempted -= 1
                stats.error, stats.error_kind = f"{type(e).__name__}: {e}", "auth"
                log.error("%s: %s", source_name, e)
                return
            except SourceError as e:
                permanent = isinstance(e, PermanentSourceError)
                self.store.mark_content_failed(doc, str(e), permanent=permanent, max_attempts=max_attempts)
                stats.failed += 1
                if len(stats.failures) < MAX_LISTED:
                    stats.failures.append(_brief(doc, error=str(e)[:300], permanent=permanent))
                self._event("content_failed", "warning", str(e)[:300], row=doc, details={"permanent": permanent})
                log.warning("content for %s failed: %s", doc_key(source_name, doc.source_id), e)
                continue
            self._save_content(doc, content, stats)
            if stats.fetched % PROGRESS_EVERY == 0:
                log.info("content from %s: %s read this run (cap %s)", source_name, stats.fetched, stats.limit)

    def _save_content(self, doc: DiscoveredDocument, content: DocumentContent, stats: FetchStats) -> None:
        profile = self.profile(doc.agency)
        assessments = {
            (t.language, t.origin): profile.assess_text(t.content)
            for t in content.texts if t.origin != Origin.SOURCE_TRANSLATION
        }
        text_id = self._with_metadata_check(profile, doc, self._id_from_texts(profile, content))
        result = self.store.save_content(doc, content, text_id, assessments)
        stats.fetched += 1
        stats.refetched += not result.first_fetch
        stats.id_from_text += text_id is not None
        stats.charged_usd += float(content.extra.get("charged_usd") or 0.0)
        row = self.store.row(doc.source, doc.source_id)

        if result.changed_texts:
            stats.content_changed.append(_brief(row, texts=result.changed_texts))
            self._event("content_changed", "warning", "text changed on re-read", row=row,
                        details={"texts": result.changed_texts})
        if result.id_change and result.id_change[0] is not None:
            old, new = result.id_change
            stats.id_changes.append(_brief(row, old=old, new=new))
            self._event("id_changed", "info", f"canonical id {old} → {new} (from the document's own text)", row=row)
        primary = [t for t in content.texts if t.language == profile.primary_language]
        if not primary:
            stats.missing_primary_text += 1
            log.warning("%s has no %s text", doc_key(doc.source, doc.source_id), profile.primary_language)
        for text in content.texts:
            if text.complete is False:
                stats.incomplete_texts.append(_brief(row, language=text.language, chars=len(text.content)))
                self._event("text_incomplete", "warning", f"{text.language} text arrived incomplete", row=row)
        kind = assessments.get((profile.primary_language, Origin.SOURCE_TEXT))
        if primary and (kind == "page" or (kind == "unknown" and profile.assesses_text)):
            stats.not_instrument.append(_brief(row, assessment=kind))
            self._event("text_not_instrument", "warning", f"{profile.primary_language} text looks like a {kind}",
                        row=row, details={"assessment": kind})
        affects = content.extra.get("affects") or []
        if affects:
            self.store.prioritize_status_checks(doc.source, affects)

    @staticmethod
    def _with_metadata_check(
        profile: AgencyProfile, doc: DiscoveredDocument, text_id: IdResolution | None
    ) -> IdResolution | None:
        """The document's own header outranks metadata, except against a high-confidence identifier:
        then it is a conflict for a person to settle (e.g. SEOJK 16/2016, whose header reads "116")."""
        if text_id is None or text_id.id is None:
            return text_id
        meta = profile.resolve_id(doc)
        if meta.id is not None and meta.confidence == "high" and meta.id != text_id.id:
            return IdResolution(text_id.id, "conflict",
                                f"{text_id.evidence} | metadata says {meta.id}: {meta.evidence}")
        return text_id

    @staticmethod
    def _id_from_texts(profile: AgencyProfile, content: DocumentContent) -> IdResolution | None:
        for text in content.texts:
            if text.language == profile.primary_language and text.origin in (Origin.SOURCE_TEXT, Origin.SELF_EXTRACTED):
                resolution = profile.resolve_id_from_text(text.content)
                if resolution.id is not None:
                    return resolution
        return None

    # --- standing checks -----------------------------------------------------------

    def check_status(self, *, sources: Iterable[str] | None = None, budget: int | None = None) -> list[StatusStats]:
        results = []
        for source_name in self._sources_in_use(sources):
            connector = self.connector(source_name)
            if not connector.supports_status_checks:
                continue
            stats = StatusStats(source_name, budget if budget is not None else self.config.monitoring.status_checks_per_run)
            self._track("status_checks", stats)
            log.info("checking whether stored documents were amended or replaced on %s (up to %s, free calls)",
                     source_name, stats.budget)
            for doc in self.store.status_check_queue(source_name, stats.budget):
                try:
                    check = connector.check_status(doc)
                except QuotaExhausted:
                    stats.paused = True
                    break
                except SourceAuthError as e:
                    stats.error, stats.error_kind = f"{type(e).__name__}: {e}", "auth"
                    break
                except SourceError as e:
                    stats.failed += 1
                    log.warning("status check for %s failed: %s", doc_key(source_name, doc.source_id), e)
                    continue
                stats.checked += 1
                if stats.checked % PROGRESS_EVERY == 0:
                    log.info("standing checks on %s: %s/%s", source_name, stats.checked, stats.budget)
                previous, previous_edges = self.store.save_status(doc, check)
                if previous is None:
                    stats.baselined += 1
                elif previous != check.fingerprint:
                    self._record_status_change(doc, check.edges, previous_edges, check.summary, stats)
            stats.partial = False
            log.info("standing of %s documents checked on %s (%s first checks, %s changed)",
                     stats.checked, source_name, stats.baselined, len(stats.changed))
            results.append(stats)
        return results

    def _record_status_change(self, doc: DiscoveredDocument, edges: list[dict[str, Any]],
                              previous_edges: list[dict[str, Any]], summary: str, stats: StatusStats) -> None:
        def keyed(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
            return {(e["relation"], e["source_id"]): e for e in items}

        now, before = keyed(edges), keyed(previous_edges)
        ids = self.store.canonical_ids(doc.source, [k[1] for k in now.keys() | before.keys()])
        added = [{**e, "canonical_id": ids.get(e["source_id"])} for k, e in now.items() if k not in before]
        removed = [{**e, "canonical_id": ids.get(e["source_id"])} for k, e in before.items() if k not in now]
        row = self.store.row(doc.source, doc.source_id)
        verbs = ", ".join(f"{e['relation'].replace('supersedes', 'replaced').replace('amends', 'amended')} by "
                          f"{e['canonical_id'] or e['title'] or e['source_id']}" for e in added) or "lineage changed"
        stats.changed.append(_brief(row, summary=summary, reason=verbs, added=added, removed=removed))
        self._event("status_changed", "info", verbs, row=row, details={"summary": summary, "added": added,
                                                                       "removed": removed})

    # --- dedupe, reparse, export -------------------------------------------------------

    def dedupe(self) -> DedupOutcome:
        outcome = resolve_duplicates(self.store, self.config.monitoring.dedup_title_overlap)
        for item in outcome.new_duplicates:
            self._event("duplicate_detected", "info", item["reason"], details=item)
        for item in outcome.new_collisions:
            self._event("id_collision", "warning", item["reason"], details=item)
        log.info("dedup: %s shared ids, %s duplicates, %s collisions", outcome.groups, outcome.duplicates,
                 outcome.collisions)
        return outcome

    def reparse(self) -> dict[str, int]:
        """Apply parser and rule changes to everything stored, without network:

        1. rebuild texts from raw snapshots (connectors that support it),
        2. re-assess texts and re-derive canonical ids.
        """
        counts = {"documents": 0, "texts_rebuilt": 0, "texts_changed": 0, "ids_changed": 0, "from_text": 0}
        for row in list(self.store.iter_documents()):
            counts["documents"] += 1
            doc = Store.to_document(row)
            profile = self.profile(row["agency"])
            if row["content_status"] == "fetched" and row["source"] in self.config.sources:
                snapshots = self.store.latest_snapshots(row["source"], row["source_id"])
                try:
                    content = self.connector(row["source"]).rebuild_content(doc, snapshots) if snapshots else None
                except NotImplementedError:
                    content = None
                if content is not None:
                    assessments = {(t.language, t.origin): profile.assess_text(t.content)
                                   for t in content.texts if t.origin != Origin.SOURCE_TRANSLATION}
                    result = self.store.save_content(doc, content, None, assessments, rebuilt=True)
                    counts["texts_rebuilt"] += len(content.texts)
                    counts["texts_changed"] += len(result.changed_texts)
                    if result.changed_texts:
                        self._event("content_changed", "info", "text re-assembled from stored snapshots",
                                    row=row, details={"texts": result.changed_texts, "rebuilt": True})
            resolution, origin = None, "metadata"
            for text in self.store.texts_for(row["source"], row["source_id"]):
                if text.origin == Origin.SOURCE_TRANSLATION:
                    continue
                content = self.store.blobs.get(text.blob_sha256).decode("utf-8")
                self.store.set_assessment(text.source, text.source_id, text.language, text.origin,
                                          profile.assess_text(content))
                if resolution is None and text.language == profile.primary_language:
                    found = self._with_metadata_check(profile, doc, profile.resolve_id_from_text(content))
                    if found is not None and found.id is not None:
                        resolution, origin = found, "text"
            if resolution is None:
                resolution = profile.resolve_id(doc)
            counts["from_text"] += origin == "text"
            old = self.store.set_identity(row["source"], row["source_id"], resolution, origin)
            new = str(resolution.id) if resolution.id else None
            if old != new:
                counts["ids_changed"] += 1
                self._event("id_changed", "info", f"canonical id {old} → {new} (re-parse)", row=self.store.row(
                    row["source"], row["source_id"]), details={"old": old, "new": new, "evidence": resolution.evidence})
        return counts

    def export(self) -> ExportStats:
        languages = {code: self.profile(code).primary_language for code in self.config.agencies}
        stats = export_corpus(self.store, self.config.data_dir / "corpus", languages)
        log.info("exported %s documents (%s unresolved, %s texts withheld for rights)",
                 stats.documents, stats.unresolved, stats.texts_withheld)
        return stats

    # --- run -----------------------------------------------------------------------------

    def run(
        self,
        *,
        full: bool | None = None,
        max_reads: int | None = None,
        agencies: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
        export: bool = True,
    ) -> dict[str, Any]:
        run_id = self._run_id = self.store.start_run("run")
        self._progress = {}
        report: dict[str, Any] = {"run_id": run_id, "started_at": utcnow(), "stages_completed": []}
        try:
            self.discover(agencies=agencies, sources=sources, full=full)
            report["stages_completed"].append("discover")
            self.fetch_content(sources=sources, max_reads=max_reads)
            report["stages_completed"].append("fetch_content")
            self.check_status(sources=sources)
            report["stages_completed"].append("check_status")
            report["dedup"] = self.dedupe().as_dict()
            report["stages_completed"].append("dedupe")
            report["export"] = self.export().as_dict() if export else None
            report["stages_completed"].append("export")
        except BaseException as e:
            # Interrupted (Ctrl-C) or crashed: everything stored so far is kept; still report what happened.
            interrupted = isinstance(e, KeyboardInterrupt)
            report["error"] = repr(e)
            log.error("run %s %s during %s; writing a partial report", run_id,
                      "interrupted" if interrupted else "crashed", self._current_stage(report))
            try:
                self._finish_run(report, forced_status="interrupted" if interrupted else "crashed")
            except BaseException:
                self.store.finish_run(run_id, "interrupted" if interrupted else "crashed", report)
            raise
        else:
            return self._finish_run(report)
        finally:
            self._run_id = None
            self._progress = None

    @staticmethod
    def _current_stage(report: dict[str, Any]) -> str:
        order = ["discover", "fetch_content", "check_status", "dedupe", "export"]
        done = report["stages_completed"]
        return next((s for s in order if s not in done), "reporting")

    def _finish_run(self, report: dict[str, Any], forced_status: str | None = None) -> dict[str, Any]:
        run_id = report["run_id"]
        for section in ("discovery", "content", "status_checks"):
            report[section] = [asdict(s) for s in (self._progress or {}).get(section, [])]
        evaluation = evaluate(report, self.config.monitoring)
        if forced_status:
            # Partial data cannot prove a problem is gone: resolve nothing.
            evaluation.checked.clear()
        notifications, notify_errors = self.alert_manager().process(evaluation, run_id)
        worst = max((severity_rank(a.severity) for a in evaluation.alerts), default=0)
        paused = any(c["paused"] for c in report["content"])
        status = forced_status or ("error" if worst >= 2 else "warning" if worst == 1 else "paused" if paused else "ok")
        report.update({
            "finished_at": utcnow(),
            "status": status,
            "alerts": [asdict(a) for a in evaluation.alerts],
            "notifications": [n.as_dict() for n in notifications],
            "notify_errors": notify_errors,
            "events": self.store.event_counts(run_id),
            "store": self.store.stats(),
        })
        report["problems"] = [a["message"] for a in report["alerts"] if severity_rank(a["severity"]) >= 2]
        if forced_status:
            report["problems"].insert(0, f"run {status} during {self._current_stage(report)}: {report['error']}")
        self._write_report(report)
        self.store.finish_run(run_id, status, report)
        return report

    def _write_report(self, report: dict[str, Any]) -> None:
        reports = self.config.data_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stamp = report["finished_at"].replace(":", "").replace("-", "")
        body = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        summary = render_summary(report)
        (reports / f"run-{stamp}-{report['run_id']}.json").write_text(body, encoding="utf-8")
        (reports / f"run-{stamp}-{report['run_id']}.md").write_text(summary, encoding="utf-8")
        (reports / "latest.json").write_text(body, encoding="utf-8")
        (reports / "latest.md").write_text(summary, encoding="utf-8")
