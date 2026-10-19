"""Command line: ``ojk-connector <command>``. Run ``ojk-connector --help`` for the list."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ojk_connector.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from ojk_connector.export_v2 import export_bundle
from ojk_connector.models import Origin, Rights
from ojk_connector.monitoring.health import check_health, exit_code
from ojk_connector.pipeline import Pipeline
from ojk_connector.sources import SourceContext, SourceError, source_class

EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE = 0, 2, 64


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ojk-connector", description="Ingest OJK regulations into a local corpus.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="pipeline config (default: %(default)s)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="sign in to sources that need it").add_subparsers(dest="auth_command", required=True)
    login = auth.add_parser("login", help="sign in and store a refreshable token")
    login.add_argument("source")
    login.add_argument("--method", choices=["browser", "device"], default="browser")
    auth.add_parser("status", help="show sign-in state").add_argument("source")

    def scope(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--source", action="append", help="limit to this source (repeatable)")

    run = sub.add_parser("run", help="the scheduled job: discover, fetch, check, dedupe, export, alert")
    scope(run)
    run.add_argument("--full", action="store_true", help="force a full discovery (otherwise every N days)")
    run.add_argument("--max-reads", type=int, help="content reads per source this run (default: from config)")
    run.add_argument("--no-export", action="store_true")

    health = sub.add_parser("health", help="is ingestion healthy? exit 0 ok / 1 warning / 2 critical")
    health.add_argument("--notify", action="store_true", help="also send a stale-run alert through the channels")

    discover = sub.add_parser("discover", help="list documents from sources (cheap; no content)")
    scope(discover)
    discover.add_argument("--full", action="store_true", help="re-list everything, not just recent windows")

    fetch = sub.add_parser("fetch", help="fetch content for queued documents, paced")
    fetch.add_argument("--source", action="append")
    fetch.add_argument("--max-reads", type=int)
    fetch.add_argument("--document", action="append", help="only this canonical id, e.g. ojk:pojk:2025:25 (repeatable)")

    status = sub.add_parser("check-status", help="check whether stored regulations were amended or replaced")
    status.add_argument("--source", action="append")
    status.add_argument("--budget", type=int, help="documents to check per source (default: from config)")

    entries = sub.add_parser("entries", help="read catalogue entries: official dates, standing, history; no files")
    entries.add_argument("--source", action="append")
    entries.add_argument("--budget", type=int, help="entries to read per source (default: from config)")

    sub.add_parser("dedupe", help="recompute duplicate and id-collision decisions")
    sub.add_parser("reparse", help="re-derive ids and text checks from stored data (after rule changes); no network")
    export = sub.add_parser("export", help="rebuild data/corpus, or write a contract v2 bundle for the platform")
    export.add_argument("--format", choices=("corpus", "v2"), default="corpus",
                        help="corpus: data/corpus/ (default) · v2: a contract v2 bundle (needs --out)")
    export.add_argument("--out", type=Path, help="v2: an empty or new directory for the bundle")
    export.add_argument("--run-id", help="v2: the run this bundle belongs to (default: the current UTC time)")
    sub.add_parser("status", help="counts by source, status, identity and rights")
    sub.add_parser("review", help="records that need a person's attention")

    events = sub.add_parser("events", help="what changed: new, changed, missing, amended, duplicates, …")
    events.add_argument("--since", help="ISO date or timestamp")
    events.add_argument("--kind", action="append")
    events.add_argument("--limit", type=int, default=100)

    rights = sub.add_parser("rights", help="relabel the rights of stored texts (e.g. after a licence decision)")
    rights.add_argument("--source", required=True)
    rights.add_argument("--origin", required=True, choices=[o.value for o in Origin])
    rights.add_argument("--set", dest="rights", required=True, choices=[r.value for r in Rights])
    return p


def _run_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "alerts": [f"[{a['severity']}] {a['message']}" for a in report["alerts"]],
        "discovery": [{k: d[k] for k in ("source", "full", "seen", "new", "changed")} |
                      {"missing": len(d["missing"]), "error": d["error"]} for d in report["discovery"]],
        "content": [{k: c[k] for k in ("source", "fetched", "refetched", "failed", "skipped_duplicates", "remaining",
                                       "pause_reason", "error")} for c in report["content"]],
        "status_checks": [{k: s[k] for k in ("source", "checked", "baselined", "failed")} |
                          {"changed": len(s["changed"])} for s in report["status_checks"]],
        "entries": [{k: e[k] for k in ("source", "read", "first_reads", "failed", "error")} |
                    {"changed": len(e["changed"])} for e in report["entries"]],
        "dedup": {k: report["dedup"][k] for k in ("duplicates", "collisions")},
        "export": report["export"],
        "report": "data/reports/latest.md",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_USAGE

    if args.command == "auth":
        if args.source not in config.sources:
            print(f"unknown source {args.source!r}; configured: {', '.join(config.sources)}", file=sys.stderr)
            return EXIT_USAGE
        source = config.sources[args.source]
        cls = source_class(source.name)
        context = SourceContext(args.source, config.credentials_dir, config.user_agent)
        try:
            if args.auth_command == "login":
                print(cls.login(source.settings, context, method=args.method))
            else:
                _print(cls.auth_status(source.settings, context))
        except (SourceError, NotImplementedError) as e:
            print(f"{args.source}: {e}", file=sys.stderr)
            return EXIT_PROBLEMS
        return EXIT_OK

    with Pipeline(config) as pipeline:
        if args.command == "run":
            report = pipeline.run(full=True if args.full else None, max_reads=args.max_reads, sources=args.source,
                                  export=not args.no_export)
            _print(_run_summary(report))
            return EXIT_PROBLEMS if report["status"] == "error" else EXIT_OK
        if args.command == "health":
            report, evaluation = check_health(pipeline.store, config.monitoring)
            if args.notify:
                _, errors = pipeline.alert_manager().process(evaluation, None)
                report["notify_errors"] = errors
            _print(report)
            return exit_code(report["status"])
        if args.command == "discover":
            results = pipeline.discover(sources=args.source, full=True if args.full else None)
            _print([asdict(r) for r in results])
            return EXIT_PROBLEMS if any(r.error for r in results) else EXIT_OK
        if args.command == "fetch":
            fetched = pipeline.fetch_content(sources=args.source, max_reads=args.max_reads, documents=args.document)
            _print([asdict(r) for r in fetched])
            return EXIT_PROBLEMS if any(r.error for r in fetched) else EXIT_OK
        if args.command == "check-status":
            checked = pipeline.check_status(sources=args.source, budget=args.budget)
            _print([asdict(s) for s in checked])
            return EXIT_PROBLEMS if any(s.error for s in checked) else EXIT_OK
        if args.command == "entries":
            read = pipeline.read_entries(sources=args.source, budget=args.budget)
            _print([asdict(e) for e in read])
            return EXIT_PROBLEMS if any(e.error for e in read) else EXIT_OK
        if args.command == "dedupe":
            _print(pipeline.dedupe().as_dict())
            return EXIT_OK
        if args.command == "reparse":
            counts = pipeline.reparse()
            counts["dedup"] = {k: v for k, v in pipeline.dedupe().as_dict().items() if k in ("duplicates", "collisions")}
            _print(counts)
            print("run `ojk-connector export` to rebuild the corpus", file=sys.stderr)
            return EXIT_OK
        if args.command == "export" and args.format == "v2":
            if args.out is None:
                print("export --format v2 needs --out DIR", file=sys.stderr)
                return EXIT_USAGE
            run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            _print(export_bundle(pipeline.store, args.out, run_id=run_id).as_dict())
            return EXIT_OK
        if args.command == "export":
            _print(pipeline.export().as_dict())
            return EXIT_OK
        if args.command == "status":
            _print(pipeline.store.stats())
            return EXIT_OK
        if args.command == "review":
            items = pipeline.store.review_items()
            _print({"counts": {k: len(v) for k, v in items.items()}, **items})
            return EXIT_OK
        if args.command == "events":
            _print(pipeline.store.events(since=args.since, kinds=args.kind, limit=args.limit))
            return EXIT_OK
        if args.command == "rights":
            n = pipeline.store.set_rights(args.source, Origin(args.origin), Rights(args.rights))
            print(f"relabelled {n} texts; run `ojk-connector export` to rebuild the corpus")
            return EXIT_OK
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
