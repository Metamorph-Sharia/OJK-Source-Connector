"""Source connectors.

A connector is chosen per source in config: ``connector = "<built-in name>"`` or
``connector = "package.module:Class"`` for any other implementation of ``Connector``.
"""

from __future__ import annotations

from regingest.connectors.base import (
    AgencyBinding,
    Connector,
    ConnectorContext,
    PermanentSourceError,
    QuotaExhausted,
    SourceAuthError,
    SourceError,
)
from regingest.plugins import resolve_class

BUILTIN_CONNECTORS = {
    "regalert": "regingest.connectors.regalert.connector:RegAlertConnector",
}


def connector_class(spec: str) -> type[Connector]:
    return resolve_class(spec, BUILTIN_CONNECTORS, Connector, "connector")


__all__ = [
    "AgencyBinding",
    "BUILTIN_CONNECTORS",
    "Connector",
    "ConnectorContext",
    "PermanentSourceError",
    "QuotaExhausted",
    "SourceAuthError",
    "SourceError",
    "connector_class",
]
