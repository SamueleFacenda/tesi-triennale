"""Dataset descriptors.

A dataset is an RDF file on disk plus the metadata the queries need: the base
ontology namespace (used for the ``base:`` prefix in every query template) and the
serialization format.

Files are referenced by absolute path and are **never** copied into the repo (they are
large and copyright-encumbered). Registered datasets are only *usable* when their file
exists locally; :func:`available` reflects that.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# RDF syntaxes we support, mapped to the rdflib/HTTP content type used when loading.
RDF_FORMATS = {
    "nt": "application/n-triples",
    "ttl": "text/turtle",
    "rdfxml": "application/rdf+xml",
}


@dataclass(frozen=True)
class Dataset:
    """A single benchmark dataset."""

    name: str
    path: str          # absolute path to the RDF file
    fmt: str           # one of RDF_FORMATS
    namespace: str     # base ontology IRI, must end with '#' or '/'

    def __post_init__(self) -> None:
        if self.fmt not in RDF_FORMATS:
            raise ValueError(f"{self.name}: unknown format {self.fmt!r}")
        if not self.namespace.endswith(("#", "/")):
            object.__setattr__(self, "namespace", self.namespace + "#")

    @property
    def content_type(self) -> str:
        return RDF_FORMATS[self.fmt]

    def available(self) -> bool:
        return Path(self.path).is_file()

    def size_bytes(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0


# Base ontology namespaces (confirmed by inspecting the files and the 3dont projects).
_HERITAGE = "http://www.semanticweb.org/matteocodiglione/ontologies/2024/9/Heritage_Ontology#"
_URBAN = "http://www.semanticweb.org/mcodi/ontologies/2024/3/Urban_Ontology#"
_CORE = "http://3DOntCore#"

_ONTOS = os.path.expanduser("~/downloads/ontos")

# Registry of known datasets. Add a new one by appending a Dataset here (or register
# extra paths at runtime via `register`). Multiple serializations of the same source
# are separate datasets so the loader path per format can be benchmarked too.
_REGISTRY: dict[str, Dataset] = {}


def register(ds: Dataset) -> Dataset:
    _REGISTRY[ds.name] = ds
    return ds


def _builtin() -> None:
    entries = [
        # name, filename, format, namespace
        ("nettuno_nt", "nettuno.nt", "nt", _HERITAGE),
        ("nettuno_rdf", "nettuno.rdf", "rdfxml", _HERITAGE),
        ("torre_modena_nt", "torre_modena.nt", "nt", _HERITAGE),
        ("torre_modena_ttl", "torre_modena.ttl", "ttl", _HERITAGE),
        ("torre_modena_rdf", "torre_modena.rdf", "rdfxml", _HERITAGE),
        ("ytu3d_nt", "ytu3d.nt", "nt", _URBAN),
        ("ytu3d_rdf", "ytu3d.rdf", "rdfxml", _URBAN),
        ("santa_chiara_ttl", "santa_chiara.ttl", "ttl", _CORE),
        ("colosseo_ttl", "colosseo_3DGraph.ttl", "ttl", _CORE),
    ]
    for name, fname, fmt, ns in entries:
        register(Dataset(name=name, path=os.path.join(_ONTOS, fname), fmt=fmt, namespace=ns))

    # Tiny in-repo fixture for smoke tests (a handful of triples, 3DOntCore namespace).
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tiny.ttl"
    register(Dataset(name="tiny", path=str(fixture), fmt="ttl", namespace=_CORE))


_builtin()


def get(name: str) -> Dataset:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown dataset {name!r}; known: {', '.join(sorted(_REGISTRY))}")


def all_datasets() -> list[Dataset]:
    return list(_REGISTRY.values())
