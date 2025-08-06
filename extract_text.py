# extract_text.py

import yaml
from pathlib import Path
from typing import Dict

import pdfminer.high_level
import markdown
from ebooklib import epub
from bs4 import BeautifulSoup


def load_config(config_path: Path) -> Dict:
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def extract_pdf_text(file_path: Path) -> str:
    try:
        return pdfminer.high_level.extract_text(str(file_path))
    except Exception as e:
        print(f"[PDF error] {file_path.name}: {e}")
        return ""


def extract_md_text(file_path: Path) -> str:
    try:
        with open(file_path, encoding='utf-8') as f:
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
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                text.append(soup.get_text())
        return "\n".join(text)
    except Exception as e:
        print(f"[EPUB error] {file_path.name}: {e}")
        return ""


def extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(file_path)
    elif suffix == ".md":
        return extract_md_text(file_path)
    elif suffix == ".epub":
        return extract_epub_text(file_path)
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
