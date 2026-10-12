"""The contract every agency profile implements.

An agency profile knows *what* an agency's documents are: their families and how to
derive a canonical identity from whatever a source gives us. It knows nothing about
how documents were fetched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal

from regingest.models import DiscoveredDocument

Confidence = Literal["high", "medium", "low", "conflict", "none"]
#: "instrument": the regulation's own text; "page": a web page about it (summary, listing); "unknown".
TextKind = Literal["instrument", "page", "unknown"]

#: Confidence levels trusted enough to merge documents under one canonical id.
TRUSTED_CONFIDENCE: frozenset[str] = frozenset({"high", "medium"})


@dataclass(frozen=True, order=True)
class CanonicalId:
    """``{agency}:{family}:{year}:{number}``, e.g. ``ojk:pojk:2026:1``."""

    agency: str
    family: str
    year: int
    number: int

    def __str__(self) -> str:
        return f"{self.agency}:{self.family}:{self.year}:{self.number}"

    @classmethod
    def parse(cls, value: str) -> CanonicalId:
        agency, family, year, number = value.split(":")
        return cls(agency, family, int(year), int(number))


@dataclass(frozen=True)
class IdResolution:
    id: CanonicalId | None
    confidence: Confidence
    evidence: str  # human-readable: which fields were used, and any disagreement

    @classmethod
    def unresolved(cls, evidence: str) -> IdResolution:
        return cls(None, "none", evidence)


class AgencyProfile(ABC):
    #: Built-in name of the profile, e.g. "ojk".
    kind: ClassVar[str]
    #: Language of the agency's authoritative texts.
    primary_language: ClassVar[str] = "id"
    #: Whether ``assess_text`` can recognise the instrument (so "unknown" is worth flagging).
    assesses_text: ClassVar[bool] = False
    #: Whether the profile has identity rules (so an unresolved id is worth flagging).
    resolves_ids: ClassVar[bool] = True

    def __init__(self, code: str) -> None:
        self.code = code  # the [agencies.<code>] key in config

    @abstractmethod
    def resolve_id(self, document: DiscoveredDocument) -> IdResolution:
        """Derive a canonical id from source metadata."""

    def resolve_id_from_text(self, text: str) -> IdResolution:
        """Derive a canonical id from the document's own text (e.g. its header)."""
        return IdResolution.unresolved("no text rules for this agency")

    def assess_text(self, text: str) -> TextKind:
        """Is this text the instrument itself, or something else (e.g. a web page about it)?"""
        return "unknown"


class GenericProfile(AgencyProfile):
    """Fallback for agencies without identity rules yet: ingest and store, but leave unresolved."""

    kind = "generic"
    resolves_ids = False

    def resolve_id(self, document: DiscoveredDocument) -> IdResolution:
        return IdResolution.unresolved(f"no identity rules for agency {self.code!r} yet")
