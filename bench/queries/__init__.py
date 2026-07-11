"""Query registry.

A :class:`Query` is a SPARQL template parameterised by the dataset's base ontology
namespace (``{namespace}``). Datasets are loaded into each engine's **default graph**,
so templates deliberately carry no ``FROM`` clause — this keeps a single query text
valid across every engine.

Add a query by decorating a module-level ``Query`` with :func:`register` in
``builtin.py`` (or at runtime). ``applies_to`` lets a query opt out of datasets whose
ontology lacks the predicates it needs (e.g. the LDBC-SPB query only runs on an SPB
dataset).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..datasets import Dataset

Kind = str  # "select" | "scalar" | "ask" | "construct"


@dataclass(frozen=True)
class Query:
    name: str
    template: str
    kind: Kind = "select"
    description: str = ""
    applies_to: Callable[[Dataset], bool] = field(default=lambda ds: True, compare=False)

    def render(self, ds: Dataset) -> str:
        return self.template.format(namespace=ds.namespace).strip() + "\n"


_REGISTRY: dict[str, Query] = {}


def register(q: Query) -> Query:
    if q.name in _REGISTRY:
        raise ValueError(f"duplicate query {q.name!r}")
    _REGISTRY[q.name] = q
    return q


def get(name: str) -> Query:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown query {name!r}; known: {', '.join(sorted(_REGISTRY))}")


def all_queries() -> list[Query]:
    return list(_REGISTRY.values())


def applicable(queries: list[Query], ds: Dataset) -> list[Query]:
    """Subset of ``queries`` whose predicates make sense for ``ds``."""
    return [q for q in queries if q.applies_to(ds)]


# populate the registry
from . import builtin  # noqa: E402,F401
