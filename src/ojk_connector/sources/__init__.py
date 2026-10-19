"""Where OJK documents come from. Each ``[sources.<name>]`` in config names one of these."""

from __future__ import annotations

from ojk_connector.sources.base import (
    DEFAULT_LOOKBACK_DAYS,
    PermanentSourceError,
    QuotaExhausted,
    Source,
    SourceAuthError,
    SourceContext,
    SourceError,
    SourceForbidden,
)
from ojk_connector.sources.jdih.source import JdihOjkSource
from ojk_connector.sources.regalert.source import RegAlertSource

SOURCES: dict[str, type[Source]] = {
    "jdih_ojk": JdihOjkSource,
    "regalert": RegAlertSource,
}


def source_class(name: str) -> type[Source]:
    if name not in SOURCES:
        raise ValueError(f"unknown source {name!r}; available: {', '.join(SOURCES)}")
    return SOURCES[name]


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "PermanentSourceError",
    "QuotaExhausted",
    "SOURCES",
    "Source",
    "SourceAuthError",
    "SourceContext",
    "SourceError",
    "SourceForbidden",
    "source_class",
]
