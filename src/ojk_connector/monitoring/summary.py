"""A short markdown summary of a run, written next to the JSON report."""

from __future__ import annotations

from typing import Any


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return out + [""]


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# ojk-connector run {report['run_id']} — {report['status'].upper()}",
        "",
        f"{report['started_at']} → {report['finished_at']}",
        "",
    ]
    if report.get("error"):
        lines += [
            f"**This run stopped early** ({report['error']}). Completed stages: "
            f"{', '.join(report.get('stages_completed') or []) or 'none'}. Everything read before the stop is "
            "stored; the next run continues from there.",
            "",
        ]
    lines += [
        "## Alerts",
        "",
    ]
    lines += _table(["severity", "alert", "message"],
                    [[a["severity"], a["code"], a["message"]] for a in report.get("alerts", [])])
    if report.get("notify_errors"):
        lines += ["**Notification errors:** " + "; ".join(report["notify_errors"]), ""]

    lines += ["## Discovery", ""]
    lines += _table(
        ["source", "mode", "window", "seen", "new", "changed", "missing", "error"],
        [[d["source"], "full" if d["full"] else "incremental", f"{d['since'] or '…'} → {d['until']}",
          d["seen"], d["new"], d["changed"], len(d.get("missing", [])), d.get("error")]
         for d in report.get("discovery", [])],
    )
    lines += ["## Content", ""]
    lines += _table(
        ["source", "read", "fetched", "re-read", "failed", "duplicates skipped", "queued", "paused"],
        [[c["source"], c["attempted"], c["fetched"], c.get("refetched", 0), c["failed"], c["skipped_duplicates"],
          c["remaining"], c["pause_reason"] or ""] for c in report.get("content", [])],
    )
    lines += ["## Standing checks", ""]
    lines += _table(
        ["source", "checked", "first check", "changed", "failed"],
        [[s["source"], s["checked"], s["baselined"], len(s.get("changed", [])), s["failed"]]
         for s in report.get("status_checks", [])],
    )
    dedup = report.get("dedup") or {}
    export = report.get("export") or {}
    lines += [
        "## Store",
        "",
        f"- Duplicates merged: {dedup.get('duplicates', 0)}; id collisions under review: {dedup.get('collisions', 0)}",
        f"- Corpus: {export.get('documents', 0)} documents ({export.get('unresolved', 0)} unresolved), "
        f"{export.get('texts_written', 0)} texts written, {export.get('texts_withheld', 0)} withheld for rights, "
        f"{export.get('without_primary_text', 0)} without primary-language text yet",
        "- Events this run: " + (", ".join(f"{k} {v}" for k, v in sorted((report.get("events") or {}).items())) or "none"),
        "",
        "Run `ojk-connector review` for the records that need a person's attention.",
        "",
    ]
    return "\n".join(lines)
