"""Alert rules, alert state (no spam, escalation, resolution), notification channels, health."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fake_regalert import FakeRegAlert, row
from test_pipeline import make_pipeline

from ojk_connector.config import MonitoringConfig
from ojk_connector.monitoring import Alert, AlertManager, Evaluation, Notification, Notifier, evaluate
from ojk_connector.monitoring.health import check_health, exit_code
from ojk_connector.monitoring.notifiers import FileNotifier, NotifierError, SlackNotifier, WebhookNotifier
from ojk_connector.store import Store

CFG = MonitoringConfig()


class Recorder(Notifier):
    kind = "recorder"
    default_min_severity = "info"

    def __init__(self, fail: bool = False) -> None:
        super().__init__({}, None)  # type: ignore[arg-type]
        self.batches: list[list[Notification]] = []
        self.fail = fail

    def send(self, notifications: list[Notification]) -> None:
        if self.fail:
            raise NotifierError("recorder: down")
        self.batches.append(notifications)

    def kinds(self) -> list[tuple[str, str]]:
        return [(n.alert.code, n.kind) for batch in self.batches for n in batch]


def condition(ev_ok: bool, severity: str = "error") -> Evaluation:
    ev = Evaluation()
    ev.condition(ev_ok, "content_failed", "regalert", severity, "regalert: content failed")
    return ev


def test_condition_notifies_once_then_reminds_escalates_and_resolves(tmp_path) -> None:
    store = Store(tmp_path)
    rec = Recorder()
    manager = AlertManager(store, [rec], renotify_hours=24)

    manager.process(condition(False), run_id=1)
    manager.process(condition(False), run_id=2)  # still open: no repeat
    assert rec.kinds() == [("content_failed", "new")]

    manager.process(condition(False, "critical"), run_id=3)
    assert rec.kinds()[-1] == ("content_failed", "escalated")

    store.db.execute("UPDATE alert_state SET last_notified_at = ?",
                     ((datetime.now(UTC) - timedelta(hours=25)).isoformat(timespec="seconds"),))
    store.db.commit()
    manager.process(condition(False, "critical"), run_id=4)
    assert rec.kinds()[-1] == ("content_failed", "reminder")

    manager.process(condition(True), run_id=5)
    assert rec.kinds()[-1] == ("content_failed", "resolved")
    assert store.open_alerts() == []


def test_condition_is_not_resolved_by_a_run_that_did_not_check_it(tmp_path) -> None:
    store = Store(tmp_path)
    manager = AlertManager(store, [Recorder()])
    manager.process(condition(False), run_id=1)
    manager.process(Evaluation(), run_id=2)  # e.g. a run limited to another source
    assert len(store.open_alerts()) == 1


def test_events_are_notified_once_per_run(tmp_path) -> None:
    store = Store(tmp_path)
    rec = Recorder()
    manager = AlertManager(store, [rec])
    for run in (1, 2):
        ev = Evaluation()
        ev.event("regulation_status_changed", "regalert", "info", "1 regulation was amended.", [{"record": "x"}])
        manager.process(ev, run_id=run)
    assert rec.kinds() == [("regulation_status_changed", "new")] * 2
    assert store.open_alerts() == []


def test_a_failing_channel_does_not_stop_the_others(tmp_path) -> None:
    store = Store(tmp_path)
    ok, broken = Recorder(), Recorder(fail=True)
    _, errors = AlertManager(store, [broken, ok]).process(condition(False), run_id=1)
    assert errors == ["recorder: down"] and ok.batches


def test_min_severity_filters_per_channel(tmp_path) -> None:
    store = Store(tmp_path)
    rec = Recorder()
    rec.min_severity = "error"
    ev = Evaluation()
    ev.event("text_incomplete", "regalert", "warning", "1 text arrived incomplete.", [{"record": "x"}])
    AlertManager(store, [rec]).process(ev, run_id=1)
    assert rec.batches == []


def _http(captured: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_slack_and_webhook_payloads(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SLACK_URL", "https://hooks.slack.example/T/B/X")
    monkeypatch.setenv("HOOK_URL", "https://ops.example/hook")
    sent: list[httpx.Request] = []
    alert = Alert("auth_failed", "critical", "regalert: sign-in failed", "regalert")
    batch = [Notification(alert, "new", 7, "2026-10-12T00:00:00+00:00")]

    SlackNotifier({"webhook_url_env": "SLACK_URL"}, tmp_path, http=_http(sent)).send(batch)
    WebhookNotifier({"url_env": "HOOK_URL"}, tmp_path, http=_http(sent)).send(batch)
    slack, hook = (json.loads(r.content) for r in sent)
    assert "[CRITICAL] regalert: sign-in failed" in slack["text"]
    assert hook["notifications"][0]["code"] == "auth_failed" and hook["notifications"][0]["run_id"] == 7


def test_missing_secret_is_a_notifier_error(tmp_path) -> None:
    with pytest.raises(NotifierError, match="webhook_url_env"):
        SlackNotifier({}, tmp_path).send([Notification(Alert("x", "error", "m"), "new", 1, "t")])


def test_file_log_records_everything(tmp_path) -> None:
    FileNotifier({}, tmp_path).send([Notification(Alert("x", "info", "m"), "new", 1, "t")])
    assert json.loads((tmp_path / "alerts.jsonl").read_text())["code"] == "x"


# --- rules ----------------------------------------------------------------------------------


def base_report(**overrides) -> dict:
    discovery = {"source": "regalert", "full": True, "seen": 977, "known_before": 977,
                 "fill_rates": {"official_url": 1.0}, "previous_fill_rates": {"official_url": 1.0},
                 "missing": [], "new_unresolved": [], "error": None, "error_kind": None}
    content = {"source": "regalert", "limit": 25, "attempted": 25, "fetched": 25, "failed": 0, "remaining": 900,
               "paused": False, "charged_usd": 0.0, "failures": [], "incomplete_texts": [], "not_instrument": [],
               "content_changed": [], "error": None, "error_kind": None}
    report = {"discovery": [discovery], "content": [content], "status_checks": [], "dedup": {}}
    for path, value in overrides.items():
        section, key = path.split(".")
        report[section][0][key] = value
    return report


def codes(report: dict) -> set[str]:
    return {a.code for a in evaluate(report, CFG).alerts}


def test_a_healthy_run_raises_nothing() -> None:
    assert codes(base_report()) == set()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"discovery.error": "SourceAuthError: expired", "discovery.error_kind": "auth"}, "auth_failed"),
        ({"content.error": "SourceAuthError: expired", "content.error_kind": "auth"}, "auth_failed"),
        ({"discovery.error": "SourceError: HTTP 500", "discovery.error_kind": "source"}, "discovery_failed"),
        ({"discovery.seen": 0}, "discovery_empty"),
        ({"discovery.seen": 600}, "discovery_drop"),
        ({"discovery.fill_rates": {"official_url": 0.4}}, "fill_rate_drop"),
        ({"content.attempted": 0, "content.fetched": 0}, "backlog_stalled"),
        ({"content.failed": 10}, "content_failure_rate"),
        ({"content.charged_usd": 0.05}, "source_charging"),
        ({"content.not_instrument": [{"record": "regalert:x"}]}, "text_not_instrument"),
        ({"discovery.missing": [{"record": "regalert:x"}]}, "documents_missing"),
    ],
)
def test_rules(overrides: dict, code: str) -> None:
    assert code in codes(base_report(**overrides))


def test_auth_failure_is_critical_and_tells_how_to_fix_it() -> None:
    ev = evaluate(base_report(**{"content.error": "x", "content.error_kind": "auth"}), CFG)
    alert = next(a for a in ev.alerts if a.code == "auth_failed")
    assert alert.severity == "critical" and "ojk-connector auth login regalert" in alert.message


def test_a_paused_backlog_is_not_stalled() -> None:
    assert "backlog_stalled" not in codes(base_report(**{"content.attempted": 0, "content.paused": True}))


# --- end to end ---------------------------------------------------------------------------------


def test_run_report_alerts_and_health(tmp_path) -> None:
    fake = FakeRegAlert(rows=[row("u1", "2026-02-23", "POJK-1-Tahun-2026-x", "x")],
                        texts={"u1": ("en", "PERATURAN OTORITAS JASA KEUANGAN\nNOMOR 1 TAHUN 2026\nTENTANG")})
    rec = Recorder()
    with make_pipeline(tmp_path, fake, notifiers=[rec]) as p:
        assert exit_code(check_health(p.store, CFG)[0]["status"]) == 2  # never run

        report = p.run()
        assert report["status"] == "ok"
        assert (tmp_path / "data/reports/latest.md").read_text().startswith(f"# ojk-connector run {report['run_id']} — OK")
        health, _ = check_health(p.store, CFG)
        assert health["status"] == "ok" and exit_code(health["status"]) == 0

        fake.token = "rotated"  # credentials stop working
        report = p.run()
        assert report["status"] == "error"
        assert ("auth_failed", "new") in rec.kinds()
        health, _ = check_health(p.store, CFG)
        assert health["status"] == "critical"  # an open critical alert

        fake.token = "good-token"
        report = p.run()
        assert ("auth_failed", "resolved") in rec.kinds()
        assert check_health(p.store, CFG)[0]["status"] == "ok"


def test_health_turns_critical_when_runs_stop(tmp_path) -> None:
    store = Store(tmp_path)
    run_id = store.start_run("run")
    store.finish_run(run_id, "ok", {})
    later = datetime.now(UTC) + timedelta(hours=CFG.max_hours_between_runs + 1)
    health, ev = check_health(store, CFG, now=later)
    assert health["status"] == "critical" and ev.alerts[0].code == "stale_run"
