"""Alert state: notify when a condition starts, escalates, keeps going, or resolves."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from regingest.monitoring.alerts import Alert, Evaluation, severity_rank
from regingest.monitoring.notifiers import Notification, Notifier, NotifierError
from regingest.store import Store, canonical_json, utcnow

log = logging.getLogger(__name__)


def _hours_between(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 3600


class AlertManager:
    def __init__(self, store: Store, notifiers: list[Notifier], *, renotify_hours: float = 24.0) -> None:
        self.store = store
        self.notifiers = notifiers
        self.renotify_hours = renotify_hours

    def process(self, evaluation: Evaluation, run_id: int | None) -> tuple[list[Notification], list[str]]:
        now = utcnow()
        notifications: list[Notification] = []
        active: set[str] = set()

        for alert in evaluation.alerts:
            fp = alert.fingerprint(run_id)
            active.add(fp)
            state = self.store.alert_state(fp)
            open_state = state is not None and state["resolved_at"] is None
            if alert.transient or not open_state:
                kind: str | None = "new"
            elif severity_rank(alert.severity) > severity_rank(state["severity"]):
                kind = "escalated"
            elif state["last_notified_at"] is None or _hours_between(state["last_notified_at"], now) >= self.renotify_hours:
                kind = "reminder"
            else:
                kind = None
            self.store.save_alert_state({
                "fingerprint": fp,
                "code": alert.code,
                "scope": alert.scope,
                "severity": alert.severity,
                "message": alert.message,
                "details_json": canonical_json(alert.details),
                "first_seen_at": state["first_seen_at"] if open_state else now,
                "last_seen_at": now,
                "last_notified_at": now if kind else state["last_notified_at"],
                "resolved_at": now if alert.transient else None,
                "notify_count": (state["notify_count"] if open_state else 0) + (1 if kind else 0),
            })
            if kind:
                notifications.append(Notification(alert, kind, run_id, now))

        for state in self.store.open_alerts():
            if state["fingerprint"] in evaluation.checked and state["fingerprint"] not in active:
                self.store.save_alert_state({"fingerprint": state["fingerprint"], "code": state["code"],
                                             "scope": state["scope"], "severity": state["severity"],
                                             "message": state["message"], "first_seen_at": state["first_seen_at"],
                                             "last_seen_at": state["last_seen_at"], "resolved_at": now})
                resolved = Alert(state["code"], state["severity"], state["message"], state["scope"],
                                 json.loads(state["details_json"] or "{}"))
                notifications.append(Notification(resolved, "resolved", run_id, now))

        return notifications, self.dispatch(notifications)

    def dispatch(self, notifications: list[Notification]) -> list[str]:
        errors = []
        for notifier in self.notifiers:
            batch = [n for n in notifications if notifier.wants(n)]
            if not batch:
                continue
            try:
                notifier.send(batch)
            except NotifierError as e:
                errors.append(str(e))
                log.error("notification via %s failed: %s", notifier.kind, e)
        return errors

    def open_alerts(self) -> list[dict[str, Any]]:
        return [
            {k: s[k] for k in ("code", "scope", "severity", "message", "first_seen_at", "last_seen_at")}
            for s in self.store.open_alerts()
        ]
