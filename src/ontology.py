"""
Ontology helper utilities for Re‑Searcher.

This module defines a simple way to work with user‑defined ontologies (conceptual frameworks).
An ontology is defined in a YAML file (see ``ontology.example.yaml`` for a reference structure)
and maps high‑level concepts to lists of related terms or synonyms.  At runtime the
ontology can be used to expand user queries or to annotate text with concept tags.

Functions
---------
load_ontology(path: str) -> dict
    Load a YAML ontology from disk and return a mapping of concept names to lists of
    synonyms.

expand_terms(query: str, ontology: dict) -> list[str]
    Given a search query and an ontology, return a list of search terms consisting
    of the original words plus any synonyms defined for those words under the
    ontology.  The matching is case‑insensitive.

You can extend this module with additional helpers, e.g. functions to tag chunks
of text with concept labels or to compute similarity scores based on concept
hierarchies.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List

import yaml


def load_ontology(path: Path | str) -> Dict[str, List[str]]:
    """Load an ontology from a YAML file.

    Parameters
    ----------
    path : Path | str
        The filesystem path to the ontology YAML.  The file should contain a top‑level
        ``concepts`` key mapping concept names to lists of synonyms.

    Returns
    -------
    Dict[str, List[str]]
        A dictionary mapping concept names (strings) to lists of synonyms.  If the
        file cannot be read or does not contain a ``concepts`` mapping the
        function returns an empty dictionary.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        concepts = data.get("concepts", {})
        # ensure all values are lists of strings
        ontology: Dict[str, List[str]] = {}
        for key, value in concepts.items():
            if isinstance(value, str):
                ontology[key] = [value]
            elif isinstance(value, Iterable):
                ontology[key] = [str(v) for v in value if v]
            else:
                ontology[key] = []
        return ontology
    except Exception:
        # fail silently – the API will still work but without ontology expansion
        return {}


def expand_terms(query: str, ontology: Dict[str, List[str]]) -> List[str]:
    """Expand the terms in a query using an ontology.

    This helper searches the query for any terms (concepts or synonyms) defined
    in the ontology. The matching is case-insensitive and respects word boundaries.
    When a match is found, the corresponding concept and all its synonyms are
    added to the result set, which also includes the original tokens from the
    query.

    Parameters
    ----------
    query : str
        The user‑entered search query.
    ontology : Dict[str, List[str]]
        A mapping of concept names to synonyms.

    Returns
    -------
    List[str]
        A list of tokens comprising the original query and any synonyms.
    """
    if not query:
        return []

    # Start with the original tokens
    expanded: set[str] = set(query.split())
    query_lower = query.lower()

    for concept, synonyms in ontology.items():
        # Create a list of all terms for a concept, including the concept name itself
        all_terms = [concept] + synonyms
        for term in all_terms:
            # Use word boundaries to match whole words or phrases.
            # This prevents matching "cat" in "caterpillar", for example.
            if re.search(r"\b" + re.escape(term.lower()) + r"\b", query_lower):
                # If a match is found, add the concept and all its synonyms
                expanded.add(concept)
                expanded.update(synonyms)
                # Once one term for a concept matches, we can stop checking other terms for the same concept
                break
    return list(expanded)


__all__ = ["load_ontology", "expand_terms"]
