"""Health check for schedulers and uptime monitors (``regingest health``).

Exit codes follow the Nagios convention: 0 OK, 1 warning, 2 critical.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from regingest.config import MonitoringConfig
from regingest.monitoring.alerts import Alert, Evaluation, severity_rank
from regingest.store import Store

SUCCESSFUL = ("ok", "warning", "paused")


def check_health(store: Store, cfg: MonitoringConfig, now: datetime | None = None) -> tuple[dict[str, Any], Evaluation]:
    now = now or datetime.now(UTC)
    last = store.last_run()
    last_ok = store.last_run(statuses=SUCCESSFUL)
    hours = None
    if last_ok:
        hours = round((now - datetime.fromisoformat(last_ok["finished_at"])).total_seconds() / 3600, 1)

    ev = Evaluation()
    if last_ok is None:
        message = "regingest has never completed a run." if last is None else "regingest has never completed a run successfully."
    else:
        message = f"No successful regingest run for {hours} hours (limit {cfg.max_hours_between_runs})."
    ev.condition(last_ok is not None and hours is not None and hours <= cfg.max_hours_between_runs,
                 "stale_run", "pipeline", "critical", message, last_success=last_ok and last_ok["finished_at"])

    open_alerts = [dict(s) for s in store.open_alerts()]
    worst = max([severity_rank(a.severity) for a in ev.alerts] + [severity_rank(a["severity"]) for a in open_alerts],
                default=0)
    status = "ok" if worst == 0 else "warning" if worst == 1 else "critical"
    backlog = {
        r["source"]: r["n"] for r in store.db.execute(
            "SELECT source, COUNT(*) AS n FROM source_documents WHERE content_status = 'pending' "
            "AND missing_since IS NULL GROUP BY source")
    }
    report = {
        "status": status,
        "checked_at": now.isoformat(timespec="seconds"),
        "last_run": last and {k: last[k] for k in ("id", "status", "started_at", "finished_at")},
        "last_successful_run": last_ok and {k: last_ok[k] for k in ("id", "status", "finished_at")},
        "hours_since_success": hours,
        "open_alerts": [
            {k: a[k] for k in ("code", "scope", "severity", "message", "first_seen_at")} for a in open_alerts
        ] + [{"code": a.code, "scope": a.scope, "severity": a.severity, "message": a.message} for a in ev.alerts],
        "backlog": backlog,
    }
    return report, ev


def exit_code(status: str) -> int:
    return {"ok": 0, "warning": 1}.get(status, 2)


__all__ = ["Alert", "check_health", "exit_code"]
