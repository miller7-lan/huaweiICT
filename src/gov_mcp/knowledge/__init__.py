"""Isolated, versioned government knowledge maintenance; M1 stores are untouched."""

# Public-document registry API; package submodules retain the local maintenance runtime.
from .registry import KnowledgeStore, encode, fingerprint
