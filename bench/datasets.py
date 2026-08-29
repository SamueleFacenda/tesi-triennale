"""Dataset descriptors.

A dataset is one **logical RDF graph**, identified by its base ontology namespace, that
may be available in several serializations (N-Triples, Turtle, RDF/XML). The different
files hold the *same* content — they are not separate datasets. Each engine picks a
serialization it can ingest (e.g. QLever/RDFox take only Turtle/N-Triples), preferring
the smallest/fastest available one.

Files are referenced by absolute path and, for the big sources, never copied into the
repo. A registered dataset is *usable* only when at least one of its format files exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# RDF syntaxes we support, mapped to their MIME type and canonical file extension.
RDF_FORMATS = {
    "nt": ("application/n-triples", "nt"),
    "ttl": ("text/turtle", "ttl"),
    "rdfxml": ("application/rdf+xml", "rdf"),
}

# Order in which engines pick a format when several are available and accepted.
# Turtle first (smallest), then N-Triples (simplest to parse), then RDF/XML.
FORMAT_PREFERENCE = ("ttl", "nt", "rdfxml")


@dataclass(frozen=True)
class ResolvedInput:
    """A concrete file chosen for one engine."""
    fmt: str
    path: str
    content_type: str
    namespace: str


@dataclass(frozen=True)
class Dataset:
    name: str
    namespace: str            # base ontology IRI, ends with '#' or '/'
    formats: dict[str, str]   # fmt -> path (same content, different serialization)
    # ontology-specific bits substituted into query templates (e.g. the coordinate-extent
    # predicate: Heritage/Urban use Has_X_Max, 3DOntCore uses Max_X)
    query_params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for fmt in self.formats:
            if fmt not in RDF_FORMATS:
                raise ValueError(f"{self.name}: unknown format {fmt!r}")
        if not self.namespace.endswith(("#", "/")):
            object.__setattr__(self, "namespace", self.namespace + "#")

    def available_formats(self) -> dict[str, str]:
        return {f: p for f, p in self.formats.items() if Path(p).is_file()}

    def available(self) -> bool:
        return bool(self.available_formats())

    def resolve(self, accepted: list[str]) -> ResolvedInput | None:
        """Pick a serialization this engine accepts. ``accepted`` is in the engine's own
        preference order (e.g. owlready2 prefers RDF/XML); None if incompatible."""
        have = self.available_formats()
        for fmt in accepted:
            if fmt in have:
                return ResolvedInput(fmt, have[fmt], RDF_FORMATS[fmt][0], self.namespace)
        return None

    def size_bytes(self) -> int:
        have = self.available_formats()
        if not have:
            return 0
        # report the smallest available file (what an all-format engine would load)
        return min(os.path.getsize(p) for p in have.values())


# Base ontology namespaces (confirmed by inspecting the files and the 3dont projects).
_HERITAGE = "http://www.semanticweb.org/matteocodiglione/ontologies/2024/9/Heritage_Ontology#"
_URBAN = "http://www.semanticweb.org/mcodi/ontologies/2024/3/Urban_Ontology#"
_CORE = "http://3DOntCore#"

_ONTOS = os.path.expanduser("~/downloads/ontos")
_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
_TESTDATA = Path(__file__).resolve().parent.parent / "testdata"

_REGISTRY: dict[str, Dataset] = {}


def register(ds: Dataset) -> Dataset:
    _REGISTRY[ds.name] = ds
    return ds


def _paths(base_dir, stem: str, fmts: dict[str, str]) -> dict[str, str]:
    return {fmt: str(Path(base_dir) / f"{stem}.{ext}") for fmt, ext in fmts.items()}


# Ontology-specific names substituted into query templates. Two differ between the
# legacy ontologies and the reference one: the coordinate-extent predicate (scalar_max_x)
# and the root of the macro-entity taxonomy (chained_segmentation, taxonomical_hierarchy).
_LEGACY_PARAMS = {"max_x": "Has_X_Max", "macro_entity": "Macro_Entities"}  # Heritage, Urban
_CORE_PARAMS = {"max_x": "Max_X", "macro_entity": "Macro_Entity"}          # 3DOntCore

# The one object whose points `select_points_in_object` asks for: pinned per dataset,
# because picking it in-query with `ORDER BY ?obj LIMIT 1` let each engine measure a
# different object (see docs/notes.md). Each IRI is the smallest in codepoint order.
# A dataset with no entry simply skips that query.
_HERITAGE_DATA = "http://www.semanticweb.org/matteocodiglione/ontologies/2024/9/Heritage_Ontology/"
_URBAN_DATA = "http://www.semanticweb.org/mcodi/ontologies/2024/3/Urban_Ontolog/"  # sic: no 'y'
_POINTS_OBJECT = {
    "nettuno": _HERITAGE_DATA + "Neptune_Temple_Paestum_predicted#Abaci_number_10_",
    "torre_modena": _HERITAGE_DATA + "torre_modena#_Heritage_Ontology.Balconies_number_10_",
    "ytu3d": _URBAN_DATA + "YTU3D#Car_number_1_",
    "mini_urban": _URBAN_DATA + "YTU3D#High_Vegetation_Entity_number_1_",
    "tiny": "http://3DOntCore#obj1",
}

# `grande`'s pinned IRI embeds the real site name, which is not disclosable, so it is read
# from the environment (see .env) instead of being committed. Unset -> the query is skipped.
if _obj := os.environ.get("KBENCH_GRANDE_POINTS_OBJECT", "").strip():
    _POINTS_OBJECT["grande"] = _obj

# Datasets renamed after a run was recorded: old key -> new key, as "old=new,old=new".
# Lets `kbench thesis` read a run whose database still holds the previous name.
DATASET_ALIASES = {
    old.strip(): new.strip()
    for old, _, new in (p.partition("=") for p in
                        os.environ.get("KBENCH_DATASET_ALIASES", "").split(","))
    if old.strip() and new.strip()
}


def canonical(name: str) -> str:
    """Map a dataset key recorded by an older run onto its current name."""
    return DATASET_ALIASES.get(name, name)


def _params(onto: dict, dataset: str) -> dict:
    """Query params for one dataset: its ontology's names plus its pinned object."""
    obj = _POINTS_OBJECT.get(dataset)
    return {**onto, **({"points_object": obj} if obj else {})}


def _builtin() -> None:
    # Big source graphs in ~/downloads/ontos (referenced by path, never copied).
    register(Dataset("nettuno", _HERITAGE, _paths(_ONTOS, "nettuno", {"nt": "nt", "rdfxml": "rdf"}),
                     _params(_LEGACY_PARAMS, "nettuno")))
    register(Dataset("torre_modena", _HERITAGE,
                     _paths(_ONTOS, "torre_modena", {"nt": "nt", "ttl": "ttl", "rdfxml": "rdf"}),
                     _params(_LEGACY_PARAMS, "torre_modena")))
    register(Dataset("ytu3d", _URBAN, _paths(_ONTOS, "ytu3d", {"nt": "nt", "rdfxml": "rdf"}),
                     _params(_LEGACY_PARAMS, "ytu3d")))
    register(Dataset("santa_chiara", _CORE, _paths(_ONTOS, "santa_chiara", {"ttl": "ttl"}),
                     _params(_CORE_PARAMS, "santa_chiara")))
    # the .nt is converted from the .ttl (see docs/datasets.md): owlready2 has no Turtle parser
    register(Dataset("grande", _CORE,
                     _paths(_ONTOS, "grande_3DGraph", {"ttl": "ttl", "nt": "nt"}),
                     _params(_CORE_PARAMS, "grande")))

    # Small real-shaped samples (head of the big files), generated locally under testdata/.
    # Fast complete test that still exercises per-engine format selection.
    register(Dataset("mini_heritage", _HERITAGE,
                     _paths(_TESTDATA, "mini_heritage", {"nt": "nt", "ttl": "ttl", "rdfxml": "rdf"}),
                     _params(_LEGACY_PARAMS, "mini_heritage")))
    register(Dataset("mini_urban", _URBAN,
                     _paths(_TESTDATA, "mini_urban", {"nt": "nt", "ttl": "ttl", "rdfxml": "rdf"}),
                     _params(_LEGACY_PARAMS, "mini_urban")))

    # Tiny synthetic fixture (committed) in all three formats — smoke tests.
    register(Dataset("tiny", _CORE,
                     _paths(_FIXTURES, "tiny", {"ttl": "ttl", "nt": "nt", "rdfxml": "rdf"}),
                     _params(_CORE_PARAMS, "tiny")))


_builtin()


def get(name: str) -> Dataset:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown dataset {name!r}; known: {', '.join(sorted(_REGISTRY))}")


def all_datasets() -> list[Dataset]:
    return list(_REGISTRY.values())
