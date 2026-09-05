"""Layer 3: Pipeline stage implementations.

Each stage implements protocols from contracts.protocols and uses
infrastructure from infra/. Stages are registered for hot-pluggable
discovery and version-based selection.
"""
from __future__ import annotations

from typing import Any

_STAGE_REGISTRY: dict[str, dict[str, type]] = {}


def register_stage(cls: type) -> type:
    """Decorator to register a stage implementation."""
    name = getattr(cls, "stage_name", None)
    version = getattr(cls, "stage_version", None)
    if name is None or version is None:
        raise TypeError(
            f"{cls.__name__} must have stage_name and stage_version class attributes"
        )
    _STAGE_REGISTRY.setdefault(name, {})[version] = cls
    return cls


def get_stage(name: str, version: str | None = None) -> type:
    """Get a stage implementation by name and optional version.

    If version is None, returns the latest version (max version string).
    """
    versions = _STAGE_REGISTRY.get(name, {})
    if not versions:
        raise KeyError(f"No stage registered with name={name!r}")
    if version is None:
        version = max(versions.keys())  # default to latest
    if version not in versions:
        raise KeyError(f"Stage {name!r} version {version!r} not found (available: {list(versions.keys())})")
    return versions[version]


def list_stages() -> dict[str, dict[str, type]]:
    """Return a copy of the full stage registry."""
    return {name: dict(vers) for name, vers in _STAGE_REGISTRY.items()}


def _auto_discover() -> None:
    """Import all stage modules to trigger registration."""
    from knowledge_mining.mining.stages import parse as _parse_mod
    from knowledge_mining.mining.stages import segment as _seg_mod
    from knowledge_mining.mining.stages import enrich as _enrich_mod
    from knowledge_mining.mining.stages import relations as _rel_mod
    from knowledge_mining.mining.stages import retrieval_units as _ru_mod
    from knowledge_mining.mining.stages import publishing as _pub_mod

    # Register discovered classes
    for _mod in [_parse_mod, _seg_mod, _enrich_mod, _rel_mod, _ru_mod, _pub_mod]:
        for _attr_name in dir(_mod):
            _cls = getattr(_mod, _attr_name)
            if isinstance(_cls, type) and hasattr(_cls, "stage_name") and hasattr(_cls, "stage_version"):
                register_stage(_cls)


# Auto-discover on import
_auto_discover()
