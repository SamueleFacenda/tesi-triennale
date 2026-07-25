"""Built-in benchmark queries.

Ported from ``threedont/app/queries.py`` (the 3dont viewer) plus the extra queries
requested for the thesis. Every template uses the ``base:`` prefix bound to the
dataset's ontology namespace and reads from the default graph (no ``FROM``).

Predicate names come from the 3D ontologies (Heritage / Urban / 3DOntCore):
``base:X/Y/Z`` + ``base:R/G/B`` on points, ``base:Constitutes`` (point -> object),
``base:Point`` / ``base:Macro_Entity`` classes, the object coordinate-extent predicate
(``base:{max_x}`` — Has_X_Max on Heritage/Urban, Max_X on 3DOntCore) and
``base:Has_Z_Lenght``.
"""

from __future__ import annotations

from . import Query, register

_PREFIXES = """
PREFIX base:<{namespace}>
PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
"""


def _q(body: str) -> str:
    return _PREFIXES + body


# 1. initial select all — every point with coordinates and (optional) colour.
register(Query(
    name="select_all",
    kind="select",
    description="All points with X/Y/Z coordinates and optional R/G/B colour.",
    template=_q("""
SELECT DISTINCT ?p ?x ?y ?z
       (COALESCE(?_r, 0) AS ?r)
       (COALESCE(?_g, 0) AS ?g)
       (COALESCE(?_b, 0) AS ?b)
WHERE {{
    ?p base:X ?x ;
       base:Y ?y ;
       base:Z ?z .
    OPTIONAL {{
        ?p base:R ?_r .
        ?p base:G ?_g .
        ?p base:B ?_b .
    }}
}}
"""),
))

# 2. scalar: for each point, the X-extent (max X) of the object it constitutes.
# The predicate differs per ontology ({max_x} = Has_X_Max for Heritage/Urban, Max_X for Core).
register(Query(
    name="scalar_max_x",
    kind="scalar",
    description="Per point, the max-X coordinate of the object it constitutes.",
    template=_q("""
SELECT DISTINCT ?p ?x
WHERE {{
    ?p base:Constitutes ?obj .
    ?obj base:{max_x} ?x .
}}
"""),
))

# 3. select: all point ids belonging to one specific object (deterministically chosen,
# so it differs per dataset without hand-configuring an object IRI).
register(Query(
    name="select_points_in_object",
    kind="select",
    description="All point ids constituting one specific object.",
    template=_q("""
SELECT ?p
WHERE {{
    {{
        SELECT ?obj WHERE {{ ?s base:Constitutes ?obj }} ORDER BY ?obj LIMIT 1
    }}
    ?p base:Constitutes ?obj .
}}
"""),
))

# 4. instance segmentation — point -> object it constitutes.
register(Query(
    name="instance_segmentation",
    kind="select",
    description="Every point and the object instance it constitutes.",
    template=_q("""
SELECT DISTINCT ?s ?x
WHERE {{
    ?s base:Constitutes ?x .
}}
"""),
))

# 5. chained segmentation — most specific Macro_Entity class of each point's object.
register(Query(
    name="chained_segmentation",
    kind="select",
    description="Point -> object -> most specific Macro_Entity subclass (leaf type).",
    template=_q("""
SELECT DISTINCT ?s ?x
WHERE {{
    ?s a base:Point .
    ?s base:Constitutes ?obj .
    ?obj a ?x .
    ?x rdfs:subClassOf* base:Macro_Entity .
    FILTER NOT EXISTS {{
        ?sub rdfs:subClassOf ?x .
        ?point rdf:type ?sub .
    }}
}}
"""),
))

# 6. max LOD — largest number of points constituting a single object.
register(Query(
    name="max_lod",
    kind="scalar",
    description="Maximum number of points constituting one object (level of detail).",
    template=_q("""
SELECT (MAX(?cnt) AS ?maxCount)
WHERE {{
  {{
    SELECT (COUNT(*) AS ?cnt)
    WHERE {{
      ?s base:Constitutes ?o .
    }}
    GROUP BY ?s
  }}
}}
"""),
))

# 7. taxonomical hierarchy — class -> ancestor chain up to Macro_Entity.
register(Query(
    name="taxonomical_hierarchy",
    kind="select",
    description="For each instantiated leaf class, its ancestor classes up to Macro_Entity.",
    template=_q("""
SELECT ?c ?sup (COUNT(DISTINCT ?sup2) AS ?depth)
WHERE {{
  {{
    SELECT DISTINCT ?c
    WHERE {{
      ?point a base:Point .
      ?point base:Constitutes ?obj .
      ?obj a ?c .
      ?c rdfs:subClassOf* base:Macro_Entity .
    }}
  }}
  ?c rdfs:subClassOf* ?sup .
  ?sup rdfs:subClassOf+ base:Macro_Entity .
  OPTIONAL {{
    ?sup rdfs:subClassOf* ?sup2 .
    ?sup2 rdfs:subClassOf* base:Macro_Entity .
  }}
}}
GROUP BY ?c ?sup
ORDER BY ?c ?depth ?sup
"""),
))

# 8. average height of the objects.
register(Query(
    name="avg_height",
    kind="scalar",
    description="Average object height, from the Has_Z_Lenght scalar property.",
    template=_q("""
SELECT (AVG(?h) AS ?avgHeight)
WHERE {{
    ?o base:Has_Z_Lenght ?h .
}}
"""),
))


# 9. LDBC Semantic Publishing Benchmark — advanced query 2.
# Targets the SPB / BBC creative-work ontology, NOT the 3D ontologies, so it only runs
# on a dataset whose namespace is SPB-shaped. Included for extensibility; skipped on the
# 3dont datasets. Register an SPB dataset (namespace containing "creativework"/"bbc")
# to activate it.
_SPB_Q2 = """
PREFIX cwork: <http://www.bbc.co.uk/ontologies/creativework/>
PREFIX bbc:   <http://www.bbc.co.uk/ontologies/bbc/>
PREFIX rdf:   <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?creativeWork ?title ?dateModified ?about ?mentions
WHERE {{
    ?creativeWork a cwork:CreativeWork ;
                  cwork:title ?title ;
                  cwork:dateModified ?dateModified ;
                  cwork:about ?about .
    OPTIONAL {{ ?creativeWork cwork:mentions ?mentions . }}
    ?creativeWork cwork:liveCoverage "true"^^<http://www.w3.org/2001/XMLSchema#boolean> .
}}
ORDER BY DESC(?dateModified)
LIMIT 50
"""


def _is_spb(ds) -> bool:
    ns = ds.namespace.lower()
    return "creativework" in ns or "bbc.co.uk" in ns or "spb" in ns


register(Query(
    name="ldbc_spb_q2",
    kind="select",
    description="LDBC Semantic Publishing Benchmark advanced query 2 (SPB datasets only).",
    template=_SPB_Q2,
    applies_to=_is_spb,
))
