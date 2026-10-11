"""Check registry. Add a check by creating a module here and decorating its class with @register."""

from __future__ import annotations

import importlib
import os
import pkgutil

from checks.base import Category, ChangeSet, Check, Finding, Severity

REGISTRY: dict[str, Check] = {}


def register(cls):
    """Class decorator that instantiates a check and adds it to the registry."""
    instance = cls()
    REGISTRY[instance.name] = instance
    return cls


def _load_all() -> None:
    for module in pkgutil.iter_modules(__path__):
        if module.name != "base":
            importlib.import_module(f"{__name__}.{module.name}")


def get_enabled_checks(enabled: list[str] | None = None) -> list[Check]:
    """Return registered checks, filtered by the given names or the ENABLED_CHECKS env var."""
    if enabled is None:
        raw = os.environ.get("ENABLED_CHECKS", "")
        enabled = [name.strip() for name in raw.split(",") if name.strip()]
    if not enabled:
        return list(REGISTRY.values())
    unknown = set(enabled) - REGISTRY.keys()
    if unknown:
        raise ValueError(f"Unknown checks in ENABLED_CHECKS: {sorted(unknown)}")
    return [REGISTRY[name] for name in enabled]


_load_all()

__all__ = [
    "REGISTRY",
    "Category",
    "ChangeSet",
    "Check",
    "Finding",
    "Severity",
    "get_enabled_checks",
    "register",
]
