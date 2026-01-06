# extract_text.py

from pathlib import Path
from typing import Dict
import signal

import docx
import html2text
import markdown
import pdfminer.high_level
import yaml
from bs4 import BeautifulSoup
from ebooklib import epub


def load_config(config_path: Path) -> Dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def timeout_handler(signum, frame):
    """Handler for timeout signal"""
    raise TimeoutError("PDF extraction took too long")


def extract_pdf_text(file_path: Path, timeout_seconds: int = 60) -> str:
    """Extract text from PDF with process-based timeout for true interruption.
    
    Uses subprocess to allow true timeout and interruption of hung extractions.
    
    Args:
        file_path: Path to PDF file
        timeout_seconds: Maximum seconds to spend extracting (default: 60 for OCR'd files)
    
    Returns:
        Extracted text or empty string if extraction fails/times out
    """
    try:
        import subprocess
        import sys
        import json
        import tempfile
        import os
        
        # Create a temporary file for the result
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            tmp_path = tmp.name
        
        try:
            # Python code to run in subprocess
            extract_code = f"""
import sys
import json
import pdfminer.high_level

try:
    text = pdfminer.high_level.extract_text(r'{str(file_path)}')
    result = {{'success': True, 'text': text}}
except Exception as e:
    result = {{'success': False, 'text': '', 'error': str(e)}}

with open(r'{tmp_path}', 'w', encoding='utf-8') as f:
    json.dump(result, f)
"""
            
            # Run extraction in subprocess with timeout
            try:
                proc = subprocess.run(
                    [sys.executable, '-c', extract_code],
                    timeout=timeout_seconds,
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                # Read result from temp file
                try:
                    if os.path.exists(tmp_path):
                        with open(tmp_path, 'r', encoding='utf-8') as f:
                            result = json.load(f)
                            return result.get('text', '')
                except:
                    pass
                
                return ""
                
            except subprocess.TimeoutExpired:
                # Process was killed due to timeout
                return ""
                
        finally:
            # Clean up temp file
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            
    except Exception as e:
        return ""


def extract_md_text(file_path: Path) -> str:
    try:
        with open(file_path, encoding="utf-8") as f:
            return markdown.markdown(f.read())
    except Exception as e:
        print(f"[MD error] {file_path.name}: {e}")
        return ""


def extract_epub_text(file_path: Path) -> str:
    try:
        book = epub.read_epub(str(file_path))
        text = []
        for item in book.get_items():
            if item.get_type() == epub.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                text.append(soup.get_text())
        return "\n".join(text)
    except Exception as e:
        print(f"[EPUB error] {file_path.name}: {e}")
        return ""


def extract_docx_text(file_path: Path) -> str:
    try:
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        print(f"[DOCX error] {file_path.name}: {e}")
        return ""


def extract_html_text(file_path: Path) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return html2text.html2text(f.read())
    except Exception as e:
        print(f"[HTML error] {file_path.name}: {e}")
        return ""


def extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(file_path)
    elif suffix == ".md":
        return extract_md_text(file_path)
    elif suffix == ".epub":
        return extract_epub_text(file_path)
    elif suffix == ".docx":
        return extract_docx_text(file_path)
    elif suffix in [".html", ".htm"]:
        return extract_html_text(file_path)
    else:
        return ""


def walk_and_extract(cfg: Dict) -> Dict[str, str]:
    docs = {}
    input_folder = Path(cfg["input_folder"]).expanduser()
    file_types = cfg["file_types"]
    log = cfg.get("log_progress", False)

    for file_path in input_folder.rglob("*"):
        if file_path.suffix.lower() in file_types:
            if log:
                print(f"Extracting: {file_path}")
            content = extract_text(file_path)
            if content.strip():
                docs[str(file_path)] = content
    return docs


if __name__ == "__main__":
    config_path = Path(__file__).parent / "config.yaml"
    cfg = load_config(config_path)
    docs = walk_and_extract(cfg)

    print(f"\n✅ Extracted {len(docs)} documents.\n")

    for path, text in docs.items():
        print(f"--- {path} ---")
        print(text[:500])  # show first 500 chars
        print("...\n")
