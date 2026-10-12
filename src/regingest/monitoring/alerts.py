"""Alert rules: turn a run report into alerts.

Two kinds of alert:
* **Conditions** (persistent): true until fixed, e.g. "sign-in failed". They are tracked
  in ``alert_state``; we notify when they start, escalate or resolve, and remind every
  ``renotify_hours`` while they last. A condition is only resolved when its check ran
  this time (``Evaluation.checked``), so a run limited to one source never "resolves"
  another source's problem.
* **Events** (transient): something happened in this run, e.g. "3 regulations were
  amended", "2 new id collisions". Notified once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from regingest.config import MonitoringConfig

SEVERITIES = ("info", "warning", "error", "critical")
MAX_DETAILS = 20


def severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str
    message: str
    scope: str = "pipeline"  # e.g. a source name, or "agency/source"
    details: dict[str, Any] = field(default_factory=dict)
    transient: bool = False

    def fingerprint(self, run_id: int | None = None) -> str:
        base = f"{self.code}:{self.scope}"
        return f"{base}:run{run_id}" if self.transient else base


@dataclass
class Evaluation:
    alerts: list[Alert] = field(default_factory=list)
    checked: set[str] = field(default_factory=set)  # fingerprints of conditions evaluated this run

    def condition(self, ok: bool, code: str, scope: str, severity: str, message: str, **details: Any) -> None:
        self.checked.add(f"{code}:{scope}")
        if not ok:
            self.alerts.append(Alert(code, severity, message, scope, details))

    def event(self, code: str, scope: str, severity: str, message: str, items: list[Any], **details: Any) -> None:
        if items:
            details = {"count": len(items), "items": items[:MAX_DETAILS], **details}
            self.alerts.append(Alert(code, severity, message, scope, details, transient=True))


def _error_condition(ev: Evaluation, stage: str, stats: dict[str, Any], scope: str) -> None:
    source = stats["source"]
    auth_failed = stats.get("error_kind") == "auth"
    ev.condition(not auth_failed, "auth_failed", source, "critical",
                 f"{source}: sign-in failed ({stage}). Run `regingest auth login {source}`.", error=stats.get("error"))
    if not auth_failed:
        ev.condition(not stats.get("error"), f"{stage}_failed", scope, "error",
                     f"{scope}: {stage} failed: {stats.get('error')}", error=stats.get("error"))


def evaluate(report: dict[str, Any], cfg: MonitoringConfig) -> Evaluation:
    ev = Evaluation()

    for d in report.get("discovery", []):
        scope = f"{d['agency']}/{d['source']}"
        _error_condition(ev, "discovery", d, scope)
        if d["full"] and not d.get("error") and not d.get("partial"):
            known = d.get("known_before", 0)
            ev.condition(not (known and d["seen"] == 0), "discovery_empty", scope, "critical",
                         f"{scope}: full discovery returned 0 documents; {known} are stored. The source may have changed.")
            floor = known * (1 - cfg.discovery_drop_threshold)
            ev.condition(not (known and 0 < d["seen"] < floor), "discovery_drop", scope, "critical",
                         f"{scope}: full discovery listed {d['seen']} documents, down from {known}.",
                         seen=d["seen"], stored=known)
            previous = d.get("previous_fill_rates") or {}
            drops = {
                f: {"before": previous[f], "now": rate}
                for f, rate in (d.get("fill_rates") or {}).items()
                if f in previous and (previous[f] - rate) * 100 >= cfg.fill_rate_drop_points
            }
            ev.condition(not drops, "fill_rate_drop", scope, "warning",
                         f"{scope}: fields are filled less often than at the last full discovery: {', '.join(drops)}. "
                         "The source's format may have changed.", fields=drops)
        ev.event("documents_missing", scope, "warning",
                 f"{scope}: {len(d.get('missing', []))} documents are no longer listed by the source.",
                 d.get("missing", []))
        ev.event("id_unresolved", scope, "warning",
                 f"{scope}: {len(d.get('new_unresolved', []))} new documents have no confident canonical id.",
                 d.get("new_unresolved", []))

    for c in report.get("content", []):
        source = c["source"]
        _error_condition(ev, "content", c, source)
        ev.condition(not c.get("charged_usd"), "source_charging", source, "critical",
                     f"{source} charged ${c.get('charged_usd', 0):.2f} for content reads; reads stopped.")
        if not c.get("partial"):  # an interrupted stage proves neither a stall nor a failure rate
            stalled = (c["remaining"] > 0 and c["limit"] > 0 and c["attempted"] == 0 and not c["paused"]
                       and not c.get("error"))
            ev.condition(not stalled, "backlog_stalled", source, "warning",
                         f"{source}: {c['remaining']} documents are queued but none were read this run.")
            rate = c["failed"] / c["attempted"] if c["attempted"] else 0.0
            ev.condition(rate <= cfg.content_failure_rate, "content_failure_rate", source, "error",
                         f"{source}: {c['failed']} of {c['attempted']} content reads failed ({rate:.0%}).")
        ev.event("content_failures", source, "warning",
                 f"{source}: {c['failed']} documents could not be read.", c.get("failures", []))
        ev.event("text_incomplete", source, "warning",
                 f"{source}: {len(c.get('incomplete_texts', []))} texts arrived incomplete.", c.get("incomplete_texts", []))
        ev.event("text_not_instrument", source, "warning",
                 f"{source}: {len(c.get('not_instrument', []))} documents have no instrument text "
                 "(e.g. only a web page about it); they need the official PDF.", c.get("not_instrument", []))
        ev.event("content_changed", source, "warning",
                 f"{source}: the text of {len(c.get('content_changed', []))} documents changed on re-read.",
                 c.get("content_changed", []))

    for s in report.get("status_checks", []):
        _error_condition(ev, "status_check", s, s["source"])
        ev.event("regulation_status_changed", s["source"], "info",
                 f"{len(s.get('changed', []))} regulations were amended or replaced.", s.get("changed", []))

    dedup = report.get("dedup") or {}
    ev.event("id_collision", "dedup", "warning",
             f"{len(dedup.get('new_collisions', []))} records share a canonical id but look like different documents.",
             dedup.get("new_collisions", []))
    ev.event("same_text_different_ids", "dedup", "warning",
             f"{len(dedup.get('same_text_different_ids', []))} texts are stored under more than one canonical id.",
             dedup.get("same_text_different_ids", []))
    return ev
