"""Tiny HTTP SPARQL wrappers for the embedded (in-process) engines.

rdflib and owlready2 have no HTTP server; these expose their SPARQL over the same HTTP
protocol the harness uses for every other engine, so serialization cost is comparable.
"""
