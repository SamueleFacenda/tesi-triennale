"""Engine implementations and registry.

Importing this package registers every built-in engine.
"""

from .registry import ENGINES, get, register_engine, available_engines  # noqa: F401

# register built-ins (import for side effects)
from . import oxigraph, fuseki, qendpoint, qlever  # noqa: F401,E402
from . import virtuoso, graphdb, rdfox, stardog     # noqa: F401,E402
from . import rdflib_engine, owlready2_engine       # noqa: F401,E402

__all__ = ["ENGINES", "get", "register_engine", "available_engines"]
