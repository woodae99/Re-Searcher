"""Abstract base class for data sources."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterator, Optional


class Document:
    """Represents a document with content and metadata."""

    def __init__(
        self,
        content: str,
        metadata: Dict[str, Any],
        doc_id: str = None,
    ):
        self.content = content
        self.metadata = metadata
        self.doc_id = doc_id or metadata.get("id", "")

    def __repr__(self):
        return f"Document(id={self.doc_id}, content_length={len(self.content)})"


# Type alias for progress callback
# Callback receives: {"event": str, "source": str, ...additional fields}
ProgressCallback = Callable[[Dict[str, Any]], None]


class DataSource(ABC):
    """Abstract base class for data sources."""

    def __init__(
        self,
        config: Dict[str, Any],
        progress_callback: Optional[ProgressCallback] = None,
    ):
        self.config = config
        self.progress_callback = progress_callback

    def _emit_progress(self, event: str, **kwargs) -> None:
        """
        Emit a progress event if callback is set.

        Args:
            event: Event type (e.g., "item_start", "item_complete", "error")
            **kwargs: Additional event data
        """
        if self.progress_callback:
            self.progress_callback({
                "event": event,
                "source": self.__class__.__name__,
                **kwargs,
            })

    @abstractmethod
    def fetch_documents(self) -> Iterator[Document]:
        """
        Fetch documents from the data source.

        Yields:
            Document objects with content and metadata.
        """
        pass

    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if this data source is enabled in config."""
        pass

    def validate_config(self) -> bool:
        """Validate the configuration for this source."""
        return True
