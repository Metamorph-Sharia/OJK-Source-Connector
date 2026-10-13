"""Pipeline configuration (``config/pipeline.toml``).

Relative paths are resolved against the directory of the config file, so the
pipeline behaves the same whatever the working directory (e.g. under cron).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ojk_connector import __version__

DEFAULT_CONFIG_PATH = Path("config/pipeline.toml")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    name: str  # also the source type, e.g. "regalert"
    settings: Mapping[str, Any]


@dataclass(frozen=True)
class MonitoringConfig:
    max_hours_between_runs: float = 36.0  # `ojk-connector health` turns critical after this
    full_discovery_every_days: int = 7  # full re-listing detects removed documents and format drift
    discovery_drop_threshold: float = 0.10  # alert when a full discovery lists 10% fewer documents
    fill_rate_drop_points: float = 20.0  # alert when a field is filled 20 points less often
    content_failure_rate: float = 0.2
    renotify_hours: float = 24.0  # reminders for conditions that stay open
    status_checks_per_run: int = 100  # standing checks (free) per source per run
    dedup_title_overlap: float = 0.8
    notify: tuple[Mapping[str, Any], ...] = ()  # [[monitoring.notify]] channels


@dataclass(frozen=True)
class Config:
    path: Path
    data_dir: Path
    credentials_dir: Path
    user_agent: str
    sources: Mapping[str, SourceConfig]  # in config order: the primary source first
    monitoring: MonitoringConfig = MonitoringConfig()


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    base = path.resolve().parent

    pipeline = raw.get("pipeline", {})
    contact = pipeline.get("contact")
    user_agent = f"ojk-connector/{__version__}" + (f" (+{contact})" if contact else "")

    sources = {name: SourceConfig(name=name, settings=dict(table)) for name, table in raw.get("sources", {}).items()}
    if not sources:
        raise ConfigError(f"{path}: no [sources.<name>] configured")

    monitoring_table = dict(raw.get("monitoring", {}))
    channels = tuple(dict(c) for c in monitoring_table.pop("notify", []))
    for channel in channels:
        if "type" not in channel:
            raise ConfigError("each [[monitoring.notify]] needs a 'type' (file, slack, webhook or email)")
    known = set(MonitoringConfig.__dataclass_fields__) - {"notify"}
    unknown = set(monitoring_table) - known
    if unknown:
        raise ConfigError(f"unknown [monitoring] settings: {', '.join(sorted(unknown))}")
    defaults = MonitoringConfig()
    monitoring = MonitoringConfig(
        **{k: type(getattr(defaults, k))(v) for k, v in monitoring_table.items()}, notify=channels
    )

    return Config(
        path=path.resolve(),
        data_dir=_resolve(base, pipeline.get("data_dir", "../data")),
        credentials_dir=_resolve(base, pipeline.get("credentials_dir", "~/.config/ojk-connector/credentials")),
        user_agent=user_agent,
        sources=sources,
        monitoring=monitoring,
    )
