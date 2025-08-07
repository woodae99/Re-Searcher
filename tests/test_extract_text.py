# tests/test_extract_text.py

from pathlib import Path
import sys
import os

# Add the src directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from extract_text import extract_docx_text, extract_html_text, extract_text

def test_extract_html_text():
    """Tests extracting text from an HTML file."""
    test_file = Path(__file__).parent / "test.html"
    text = extract_html_text(test_file)
    assert "This is a heading" in text
    assert "This is a paragraph." in text

def test_extract_text_dispatcher_html():
    """Tests that the main extract_text function dispatches to the correct extractor for HTML."""
    html_file = Path(__file__).parent / "test.html"
    html_text = extract_text(html_file)
    assert "This is a heading" in html_text
