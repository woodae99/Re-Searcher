"""Data sources for research library."""

from .base import DataSource, Document
from .obsidian import ObsidianSource
from .zotero import ZoteroSource

__all__ = ["DataSource", "Document", "ZoteroSource", "ObsidianSource"]
