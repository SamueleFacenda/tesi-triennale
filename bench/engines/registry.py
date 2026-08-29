"""Engine registry — mirrors 3DOnt's ``StorageFactory.register`` decorator pattern."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import AbstractEngine

ENGINES: dict[str, type["AbstractEngine"]] = {}


def register_engine(name: str):
    def inner(cls: type["AbstractEngine"]) -> type["AbstractEngine"]:
        cls.name = name
        ENGINES[name] = cls
        return cls
    return inner


def get(name: str) -> type["AbstractEngine"]:
    try:
        return ENGINES[name]
    except KeyError:
        raise KeyError(f"unknown engine {name!r}; known: {', '.join(sorted(ENGINES))}")


def available_engines() -> dict[str, tuple[bool, str]]:
    """Map engine name -> (available, reason)."""
    return {name: cls.available() for name, cls in sorted(ENGINES.items())}
