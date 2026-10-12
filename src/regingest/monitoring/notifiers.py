"""Notification channels. Chosen in config like connectors: a built-in ``type`` or
``"package.module:Class"``. A failing channel never fails the run; its error is reported.

Secrets (webhook URLs, SMTP passwords) are read from environment variables named in
config, never stored in config itself.
"""

from __future__ import annotations

import json
import os
import smtplib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import httpx

from regingest.monitoring.alerts import Alert, severity_rank
from regingest.plugins import resolve_class


class NotifierError(Exception):
    pass


@dataclass(frozen=True)
class Notification:
    alert: Alert
    kind: str  # new | escalated | reminder | resolved
    run_id: int | None
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "run_id": self.run_id, "at": self.at, **asdict(self.alert)}

    def line(self) -> str:
        label = "RESOLVED" if self.kind == "resolved" else self.alert.severity.upper()
        suffix = {"escalated": " (escalated)", "reminder": " (still open)"}.get(self.kind, "")
        return f"[{label}] {self.alert.message}{suffix}"


def render_text(notifications: list[Notification], *, max_items: int = 5) -> str:
    lines = []
    for n in notifications:
        lines.append(n.line())
        for item in (n.alert.details.get("items") or [])[:max_items]:
            lines.append(f"    - {_describe(item)}")
        extra = n.alert.details.get("count", 0) - max_items
        if extra > 0:
            lines.append(f"    … and {extra} more")
    return "\n".join(lines)


def _describe(item: Any) -> str:
    if not isinstance(item, Mapping):
        return str(item)
    label = item.get("canonical_id") or item.get("record") or item.get("source_id") or ""
    title = item.get("title") or ""
    reason = item.get("reason") or item.get("summary") or item.get("error") or ""
    return " — ".join(str(p) for p in (label, title[:100], reason) if p)


class Notifier(ABC):
    kind: ClassVar[str]
    default_min_severity: ClassVar[str] = "warning"

    def __init__(self, settings: Mapping[str, Any], data_dir: Path, *, http: httpx.Client | None = None) -> None:
        self.settings = settings
        self.data_dir = data_dir
        self.min_severity = settings.get("min_severity", self.default_min_severity)
        self._http = http

    def wants(self, notification: Notification) -> bool:
        return severity_rank(notification.alert.severity) >= severity_rank(self.min_severity)

    @abstractmethod
    def send(self, notifications: list[Notification]) -> None:
        """Deliver a batch (already filtered by ``wants``)."""

    def _secret(self, key: str, *, required: bool = True) -> str | None:
        env = self.settings.get(f"{key}_env")
        value = os.environ.get(env) if env else self.settings.get(key)
        if required and not value:
            raise NotifierError(f"{self.kind}: set {key}_env to an environment variable holding the {key}")
        return value

    def _post(self, url: str, payload: dict[str, Any], headers: Mapping[str, str] | None = None) -> None:
        client = self._http or httpx.Client(timeout=15.0)
        try:
            response = client.post(url, json=payload, headers=dict(headers or {}))
        except httpx.HTTPError as e:
            raise NotifierError(f"{self.kind}: {e}") from e
        finally:
            if self._http is None:
                client.close()
        if response.status_code >= 300:
            raise NotifierError(f"{self.kind}: HTTP {response.status_code} {response.text[:200]}")


class FileNotifier(Notifier):
    """Appends every notification to ``data/alerts.jsonl``. Always on."""

    kind = "file"
    default_min_severity = "info"

    def send(self, notifications: list[Notification]) -> None:
        path = Path(self.settings.get("path") or self.data_dir / "alerts.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for n in notifications:
                f.write(json.dumps(n.as_dict(), ensure_ascii=False, default=str) + "\n")


class SlackNotifier(Notifier):
    """Slack incoming webhook: ``webhook_url_env = "REGINGEST_SLACK_WEBHOOK_URL"``."""

    kind = "slack"

    def send(self, notifications: list[Notification]) -> None:
        title = self.settings.get("title", "regingest")
        self._post(self._secret("webhook_url"), {"text": f"*{title}*\n```{render_text(notifications)}```"})


class WebhookNotifier(Notifier):
    """Generic JSON webhook: POSTs ``{"source": "regingest", "notifications": [...]}``."""

    kind = "webhook"

    def send(self, notifications: list[Notification]) -> None:
        headers = {}
        if token := self._secret("bearer_token", required=False):
            headers["Authorization"] = f"Bearer {token}"
        self._post(self._secret("url"), {"source": "regingest", "notifications": [n.as_dict() for n in notifications]},
                   headers)


class EmailNotifier(Notifier):
    """SMTP email: ``smtp_host``, ``smtp_port``, ``from``, ``to``; credentials via ``username_env``/``password_env``."""

    kind = "email"

    def send(self, notifications: list[Notification]) -> None:
        to = self.settings.get("to")
        recipients = [to] if isinstance(to, str) else list(to or [])
        if not recipients or not self.settings.get("from") or not self.settings.get("smtp_host"):
            raise NotifierError("email: set smtp_host, from and to")
        worst = max(notifications, key=lambda n: severity_rank(n.alert.severity))
        message = EmailMessage()
        message["Subject"] = f"{self.settings.get('subject_prefix', '[regingest]')} {worst.line()[:150]}"
        message["From"] = self.settings["from"]
        message["To"] = ", ".join(recipients)
        message.set_content(render_text(notifications, max_items=MAX_EMAIL_ITEMS))
        try:
            with smtplib.SMTP(self.settings["smtp_host"], int(self.settings.get("smtp_port", 587)), timeout=30) as smtp:
                if self.settings.get("starttls", True):
                    smtp.starttls()
                user = self._secret("username", required=False)
                if user:
                    smtp.login(user, self._secret("password") or "")
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as e:
            raise NotifierError(f"email: {e}") from e


MAX_EMAIL_ITEMS = 20

BUILTIN_NOTIFIERS = {
    "file": "regingest.monitoring.notifiers:FileNotifier",
    "slack": "regingest.monitoring.notifiers:SlackNotifier",
    "webhook": "regingest.monitoring.notifiers:WebhookNotifier",
    "email": "regingest.monitoring.notifiers:EmailNotifier",
}


def build_notifiers(configs: list[Mapping[str, Any]], data_dir: Path) -> list[Notifier]:
    """The always-on file log plus every configured channel."""
    notifiers: list[Notifier] = [FileNotifier({}, data_dir)]
    for cfg in configs:
        settings = dict(cfg)
        cls = resolve_class(settings.pop("type", ""), BUILTIN_NOTIFIERS, Notifier, "notifier")
        notifiers.append(cls(settings, data_dir))
    return notifiers
