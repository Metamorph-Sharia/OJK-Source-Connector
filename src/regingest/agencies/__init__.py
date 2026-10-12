"""Agency profiles.

A profile is chosen per agency in config: ``profile = "<built-in name>"`` or
``profile = "package.module:Class"``. Agencies without rules yet use ``"generic"``.
"""

from __future__ import annotations

from regingest.agencies.base import (
    TRUSTED_CONFIDENCE,
    AgencyProfile,
    CanonicalId,
    GenericProfile,
    IdResolution,
    TextKind,
)
from regingest.plugins import resolve_class

BUILTIN_PROFILES = {
    "generic": "regingest.agencies.base:GenericProfile",
    "ojk": "regingest.agencies.ojk:OJKProfile",
}


def profile_class(spec: str) -> type[AgencyProfile]:
    return resolve_class(spec, BUILTIN_PROFILES, AgencyProfile, "agency profile")


__all__ = [
    "AgencyProfile",
    "BUILTIN_PROFILES",
    "CanonicalId",
    "GenericProfile",
    "IdResolution",
    "TRUSTED_CONFIDENCE",
    "TextKind",
    "profile_class",
]
