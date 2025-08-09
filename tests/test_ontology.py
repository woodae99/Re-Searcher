from pathlib import Path

import pytest
import yaml

from src.ontology import expand_terms, load_ontology


@pytest.fixture
def ontology_file(tmp_path: Path) -> Path:
    """Create a dummy ontology file for testing."""
    content = {
        "concepts": {
            "emergence": ["emergentism", "self-organization", "self organization"],
            "complexity": ["complex systems", "systems theory", "complexity science"],
        }
    }
    file_path = tmp_path / "ontology.yaml"
    with file_path.open("w", encoding="utf-8") as f:
        yaml.dump(content, f)
    return file_path


def test_load_ontology_success(ontology_file: Path):
    """Test loading a valid ontology file."""
    ontology = load_ontology(ontology_file)
    assert "emergence" in ontology
    assert "complexity" in ontology
    assert ontology["emergence"] == [
        "emergentism",
        "self-organization",
        "self organization",
    ]


def test_load_ontology_file_not_found():
    """Test loading a non-existent ontology file."""
    ontology = load_ontology("non_existent_file.yaml")
    assert ontology == {}


def test_load_ontology_empty_file(tmp_path: Path):
    """Test loading an empty ontology file."""
    file_path = tmp_path / "empty.yaml"
    file_path.touch()
    ontology = load_ontology(file_path)
    assert ontology == {}


def test_load_ontology_no_concepts_key(tmp_path: Path):
    """Test loading an ontology file with no 'concepts' key."""
    content = {"other_key": "value"}
    file_path = tmp_path / "no_concepts.yaml"
    with file_path.open("w", encoding="utf-8") as f:
        yaml.dump(content, f)
    ontology = load_ontology(file_path)
    assert ontology == {}


@pytest.fixture
def sample_ontology() -> dict:
    """A sample ontology for testing expand_terms."""
    return {
        "emergence": ["emergentism", "self-organization"],
        "complexity": ["complex systems", "systems theory"],
    }


def test_expand_terms_no_expansion(sample_ontology: dict):
    """Test a query that should not be expanded."""
    query = "A simple query"
    expanded = expand_terms(query, sample_ontology)
    assert set(expanded) == {"A", "simple", "query"}


def test_expand_terms_single_word_synonym(sample_ontology: dict):
    """Test expansion with a single-word synonym."""
    query = "What is emergentism?"
    expanded = expand_terms(query, sample_ontology)
    assert "emergence" in expanded
    assert "self-organization" in expanded
    assert "emergentism" in expanded


def test_expand_terms_multi_word_synonym(sample_ontology: dict):
    """Test expansion with a multi-word synonym."""
    query = "Tell me about complex systems"
    expanded = expand_terms(query, sample_ontology)
    assert "complexity" in expanded
    assert "complex systems" in expanded
    assert "systems theory" in expanded
    assert "Tell" in expanded  # a non-ontology word


def test_expand_terms_case_insensitive(sample_ontology: dict):
    """Test that expansion is case-insensitive."""
    query = "Tell me about Complex Systems"
    expanded = expand_terms(query, sample_ontology)
    assert "complexity" in expanded
    assert "complex systems" in expanded
    assert "systems theory" in expanded


def test_expand_terms_concept_name_match(sample_ontology: dict):
    """Test matching the concept name itself."""
    query = "The study of complexity"
    expanded = expand_terms(query, sample_ontology)
    assert "complexity" in expanded
    assert "complex systems" in expanded
    assert "systems theory" in expanded


def test_expand_terms_multiple_concepts(sample_ontology: dict):
    """Test a query with multiple concepts."""
    query = "The emergence of complex systems"
    expanded = expand_terms(query, sample_ontology)
    # Check for emergence concept
    assert "emergence" in expanded
    assert "emergentism" in expanded
    assert "self-organization" in expanded
    # Check for complexity concept
    assert "complexity" in expanded
    assert "complex systems" in expanded
    assert "systems theory" in expanded


def test_expand_terms_empty_query(sample_ontology: dict):
    """Test an empty query string."""
    assert expand_terms("", sample_ontology) == []


def test_expand_terms_empty_ontology():
    """Test with an empty ontology."""
    query = "A query with no ontology"
    expanded = expand_terms(query, {})
    assert set(expanded) == set(query.split())
