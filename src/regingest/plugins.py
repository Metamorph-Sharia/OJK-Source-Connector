"""Loading plug-ins (connectors, agency profiles) by built-in name or dotted path."""

from __future__ import annotations

import importlib
from collections.abc import Mapping


def load_object(spec: str) -> object:
    """Import ``"package.module:attribute"`` and return the attribute."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"expected 'package.module:Name', got {spec!r}")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ValueError(f"{module_name!r} has no attribute {attr!r}") from None


def resolve_class(spec: str, builtins: Mapping[str, str], base: type, kind: str) -> type:
    """Resolve a built-in name or a dotted path to a subclass of ``base``."""
    target = builtins.get(spec, spec)
    if ":" not in target:
        known = ", ".join(sorted(builtins)) or "none"
        raise ValueError(f"unknown {kind} {spec!r}; built-ins: {known}; or use 'package.module:Class'")
    cls = load_object(target)
    if not (isinstance(cls, type) and issubclass(cls, base)):
        raise TypeError(f"{target!r} is not a {base.__name__}")
    return cls
