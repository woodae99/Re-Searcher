"""Durable JSON file writes with fsync for crash safety.

All checkpoint/state writers in Re-Searcher should use this module
instead of bare ``json.dump`` so that a crash or SIGKILL never leaves
a blank or truncated JSON file behind.

Semantics
---------
1. Write to a ``.tmp`` sidecar in the same directory.
2. ``json.dump`` + ``flush``.
3. ``os.fsync`` on the file descriptor.
4. Atomic ``Path.replace`` to the target path.
5. Best-effort ``fsync`` on the parent directory (ignored on failure).

SQLite is not covered here — it manages its own durability settings.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fsync_directory(path: Path) -> None:
    """Best-effort fsync of the directory containing *path*."""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_json_durable(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    fsync_dir: bool = True,
) -> None:
    """Write *payload* as JSON to *path* with crash safety.

    The file is written to a temporary sidecar, fsync'd, then atomically
    renamed into place.  On any failure the original file (if it existed)
    is preserved — callers should never see a blank or truncated checkpoint.

    Parameters
    ----------
    path:
        Destination file path.
    payload:
        Object serialisable by ``json.dump``.
    indent:
        JSON indentation (default 2).
    fsync_dir:
        Also fsync the parent directory so the rename is durable
        (default True).
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_fd = None  # track the raw fd for cleanup on error
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(path.parent),
            suffix=".tmp",
            prefix=path.name + ".",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as tmp_file:
            tmp_fd = tmp_file.fileno()
            json.dump(payload, tmp_file, indent=indent, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_fd)
            # Atomic rename inside the context manager so we have the
            # file handle available.
            actual_tmp = Path(tmp_file.name)
            actual_tmp.rename(path)
            # Cleanup in case rename didn't remove the sidecar
            try:
                actual_tmp.unlink(missing_ok=True)
            except OSError:
                pass

        if fsync_dir:
            _fsync_directory(path)
    except Exception:
        # If the sidecar write fails completely, the original file (if any)
        # is untouched — we do NOT want to corrupt a valid checkpoint.
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def write_json_durable_safe(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    fallback_direct: bool = True,
) -> bool:
    """Like ``write_json_durable`` but silently falls back to direct write.

    Returns ``True`` if the durable path succeeded, ``False`` if it fell
    back.  Useful for best-effort snapshots where a crash would only lose
    a live-progress update, not a checkpoint.
    """
    try:
        write_json_durable(path, payload, indent=indent)
        return True
    except Exception:
        if fallback_direct:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=indent, ensure_ascii=False)
                    f.flush()
                return False
            except Exception:
                return False
        raise
