"""Obsidian vault data source for extracting markdown notes."""

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

import yaml

from .base import DataSource, Document, ProgressCallback, UnitState


class ObsidianSource(DataSource):
    """Data source for Obsidian vault."""

    def __init__(
        self,
        config: Dict[str, Any],
        progress_callback: Optional[ProgressCallback] = None,
    ):
        super().__init__(config, progress_callback)
        self.obsidian_config = config.get("obsidian", {})
        self.vault_path = None

        if self.is_enabled():
            vault_path_str = self.obsidian_config.get("vault_path", "")
            if vault_path_str:
                self.vault_path = Path(vault_path_str).expanduser()

    def is_enabled(self) -> bool:
        """Check if Obsidian source is enabled."""
        return self.obsidian_config.get("enabled", False)

    def validate_config(self) -> bool:
        """Validate Obsidian configuration."""
        if not self.is_enabled():
            return True

        if not self.vault_path:
            print("[WARN] Obsidian vault path not configured")
            return False

        if not self.vault_path.exists():
            print(f"[WARN] Obsidian vault not found: {self.vault_path}")
            return False

        return True

    def fetch_documents(self, relative_paths: Optional[List[str]] = None) -> Iterator[Document]:
        """
        Fetch markdown documents from the Obsidian vault.

        Args:
            relative_paths: When given, only these vault-relative paths are
                processed (delta mode). The wikilink map is still built from
                the whole vault so links resolve correctly.

        Yields:
            Document objects for each processed markdown file.
        """
        if not self.validate_config():
            return

        include_folders = self._normalize_folder_list(
            self.obsidian_config.get("include_folders", [])
        )
        exclude_patterns = self._get_exclude_patterns()

        # Find all markdown files
        all_md_files = self._find_markdown_files(include_folders, exclude_patterns)
        link_map = self._build_link_map(all_md_files)

        if relative_paths is not None:
            wanted = {str(path) for path in relative_paths}
            md_files = [
                md_file
                for md_file in all_md_files
                if str(md_file.relative_to(self.vault_path)) in wanted
            ]
            total_files = len(md_files)
            print(
                f"[INFO] Obsidian delta: processing {total_files} of "
                f"{len(all_md_files)} markdown files"
            )
        else:
            md_files = all_md_files
            total_files = len(md_files)
            print(f"[INFO] Found {total_files} markdown files in Obsidian vault")

        # Emit source initialization
        self._emit_progress("source_init", total=total_files)

        for idx, md_file in enumerate(md_files):
            self._emit_progress("item_start", file_path=str(md_file), index=idx, total=total_files)

            try:
                document = self._process_markdown_file(md_file, link_map)
                if document:
                    yield document
                    self._emit_progress(
                        "item_complete",
                        file_path=str(md_file),
                        index=idx,
                        total=total_files,
                        status="success"
                    )
                else:
                    self._emit_progress(
                        "item_complete",
                        file_path=str(md_file),
                        index=idx,
                        total=total_files,
                        status="empty"
                    )
            except Exception as e:
                print(f"  Warning: Error processing {md_file.name}: {e}")
                self._emit_progress(
                    "item_error",
                    file_path=str(md_file),
                    index=idx,
                    error=str(e)
                )

        self._emit_progress("source_complete")

    def get_file_states(self) -> Dict[str, tuple[float, int]]:
        """Snapshot of vault files: relative path -> (mtime, size).

        Used by the pipeline's Obsidian delta to detect new, changed, and
        deleted notes without reading file contents.
        """
        if not self.validate_config():
            return {}

        include_folders = self._normalize_folder_list(
            self.obsidian_config.get("include_folders", [])
        )
        exclude_patterns = self._get_exclude_patterns()
        states: Dict[str, tuple[float, int]] = {}
        for md_file in self._find_markdown_files(include_folders, exclude_patterns):
            try:
                stat = md_file.stat()
            except OSError:
                continue
            relative = str(md_file.relative_to(self.vault_path))
            states[relative] = (stat.st_mtime, stat.st_size)
        return states

    @staticmethod
    def content_version_for_state(state: tuple[float, int]) -> str:
        """Stable content-version string from a (mtime, size) state."""
        mtime, size = state
        return f"{mtime:.6f}-{size}"

    def enumerate_state(self) -> Dict[str, UnitState]:
        """One ``vault_file`` unit per markdown note, fingerprinted by mtime:size.

        Mirrors the source-identity rule: a note's identity is
        ``source_id = obsidian-<relative_path>``; its unit_id is
        ``obsidian:<relative_path>``.
        """
        units: Dict[str, UnitState] = {}
        for relative, state in self.get_file_states().items():
            identity_value = f"obsidian-{relative}"
            unit_id = f"obsidian:{relative}"
            units[unit_id] = UnitState(
                unit_id=unit_id,
                identity_field="source_id",
                identity_value=identity_value,
                unit_kind="vault_file",
                fingerprint=self.content_version_for_state(state),
            )
        return units

    def _find_markdown_files(
        self, include_folders: List[str], exclude_patterns: List[str]
    ) -> List[Path]:
        """Find all markdown files in the vault."""
        md_files = []

        if include_folders:
            # Search only in specified folders
            for folder in include_folders:
                folder_path = self.vault_path / folder
                if folder_path.exists():
                    md_files.extend(folder_path.rglob("*.md"))
        else:
            # Search entire vault
            md_files = list(self.vault_path.rglob("*.md"))

        # Filter out excluded folders
        filtered_files = []
        for md_file in md_files:
            relative_path = md_file.relative_to(self.vault_path)
            if not self._is_excluded(relative_path, exclude_patterns):
                filtered_files.append(md_file)

        return filtered_files

    def _normalize_folder_list(self, folders: Any) -> List[str]:
        """Normalize configured folder lists and ignore blank entries."""
        if not folders:
            return []
        if isinstance(folders, str):
            folders = [folders]

        normalized = []
        for folder in folders:
            value = str(folder).strip().replace("\\", "/").strip("/")
            if value:
                normalized.append(value)
        return normalized

    def _get_exclude_patterns(self) -> List[str]:
        """Return configured excludes plus optional Obsidian user ignore filters."""
        exclude_patterns = self._normalize_folder_list(
            self.obsidian_config.get("exclude_folders", [])
        )

        if self.obsidian_config.get("use_obsidian_ignore_filters", False):
            exclude_patterns.extend(self._load_obsidian_user_ignore_filters())

        return list(dict.fromkeys(exclude_patterns))

    def _load_obsidian_user_ignore_filters(self) -> List[str]:
        """Load Obsidian's userIgnoreFilters from .obsidian/app.json."""
        if not self.vault_path:
            return []

        app_json_path = self.vault_path / ".obsidian" / "app.json"
        if not app_json_path.exists():
            print(f"[WARN] Obsidian app.json not found: {app_json_path}")
            return []

        try:
            with open(app_json_path, "r", encoding="utf-8") as f:
                app_config = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[WARN] Could not read Obsidian userIgnoreFilters: {e}")
            return []

        ignore_filters = app_config.get("userIgnoreFilters", [])
        if not isinstance(ignore_filters, list):
            return []
        return self._normalize_folder_list(ignore_filters)

    def _is_excluded(self, relative_path: Path, exclude_patterns: List[str]) -> bool:
        """Return True when a vault-relative path matches an exclude pattern."""
        relative_posix = relative_path.as_posix()
        path_parts = set(relative_path.parts)

        for raw_pattern in exclude_patterns:
            pattern = str(raw_pattern).strip().replace("\\", "/")
            normalized = pattern.strip("/")
            if not normalized:
                continue

            if normalized in path_parts:
                return True

            if relative_posix == normalized or relative_posix.startswith(f"{normalized}/"):
                return True

            if fnmatch.fnmatch(relative_posix, normalized):
                return True

        return False

    def _process_markdown_file(
        self, md_file: Path, link_map: Dict[str, str]
    ) -> Document:
        """Process a single markdown file."""
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse frontmatter and extract metadata
        frontmatter, body = self._parse_frontmatter(content)

        # Extract wikilinks and backlinks
        wikilinks = self._extract_wikilinks(body)
        resolved_links = self._resolve_wikilinks(wikilinks, link_map)
        inline_tags = self._extract_inline_tags(body)
        frontmatter_tags = self._extract_frontmatter_tags(frontmatter)
        tags = self._normalize_tags(frontmatter_tags | inline_tags)
        zotero_keys = self._extract_zotero_keys(frontmatter, body)

        # Extract aliases from frontmatter
        aliases = self._extract_aliases(frontmatter)

        # Check for code blocks
        contains_code = self._has_code_blocks(body)

        # Extract headings for heading_path context
        headings = self._extract_headings(body)
        # Build heading path at document start (position 0)
        # Individual chunks will get their own heading_path during chunking
        heading_path = self._build_heading_path(headings, len(body)) if headings else ""

        # Get relative path from vault root
        relative_path = md_file.relative_to(self.vault_path)

        try:
            stat = md_file.stat()
            content_version = self.content_version_for_state((stat.st_mtime, stat.st_size))
        except OSError:
            content_version = ""

        # Build metadata
        metadata = {
            "source_type": "obsidian",
            "vault_path": str(self.vault_path),
            "file_path": str(md_file),
            "relative_path": str(relative_path),
            "content_version": content_version,
            "file_name": md_file.name,
            "title": frontmatter.get("title", md_file.stem),
            "wikilinks": wikilinks,
            "links_out": resolved_links,
            "tags": tags,
            "backlink": f"obsidian://open?vault={self.vault_path.name}&file={relative_path}",
            "frontmatter": frontmatter,
            "contains_code": contains_code,
            "heading_count": len(headings),
        }

        # Add aliases if present
        if aliases:
            metadata["aliases"] = aliases

        # Add heading_path if we have headings
        if heading_path:
            metadata["heading_path"] = heading_path

        # Add frontmatter fields to metadata
        for key, value in frontmatter.items():
            if key not in metadata:
                metadata[key] = value

        if zotero_keys:
            metadata["zotero_key"] = zotero_keys[0] if len(zotero_keys) == 1 else zotero_keys

        return Document(
            content=body,
            metadata=metadata,
            doc_id=f"obsidian-{relative_path}",
        )

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """
        Parse YAML frontmatter from markdown content.

        Returns:
            Tuple of (frontmatter_dict, body_without_frontmatter)
        """
        frontmatter = {}
        body = content

        # Check for YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_text = parts[1].strip()
                body = parts[2].strip()

                try:
                    loaded = yaml.safe_load(frontmatter_text)
                    if isinstance(loaded, dict):
                        frontmatter = loaded
                except yaml.YAMLError:
                    frontmatter = {}

        return frontmatter, body

    def _extract_inline_tags(self, content: str) -> Set[str]:
        pattern = re.compile(r"(?<!\w)#([\w/-]+)")
        return set(pattern.findall(content))

    def _extract_frontmatter_tags(self, frontmatter: Dict[str, Any]) -> Set[str]:
        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            return {tags}
        if isinstance(tags, list):
            return {str(tag) for tag in tags}
        return set()

    def _normalize_tags(self, tags: Set[str]) -> List[str]:
        normalized = []
        for tag in tags:
            tag_text = str(tag).lstrip("#").strip().lower()
            if tag_text:
                normalized.append(tag_text)
        return sorted(set(normalized))

    def _extract_zotero_keys(self, frontmatter: Dict[str, Any], content: str) -> List[str]:
        zotero_keys = []
        frontmatter_key = frontmatter.get("zotero_key") or frontmatter.get("citekey")
        if frontmatter_key:
            if isinstance(frontmatter_key, list):
                zotero_keys.extend(str(key) for key in frontmatter_key)
            else:
                zotero_keys.append(str(frontmatter_key))

        inline_keys = re.findall(r"@([A-Za-z0-9_:-]+)", content)
        zotero_keys.extend(inline_keys)

        return list(dict.fromkeys(zotero_keys))

    def _extract_aliases(self, frontmatter: Dict[str, Any]) -> List[str]:
        """Extract aliases from frontmatter."""
        aliases = frontmatter.get("aliases", [])
        if isinstance(aliases, str):
            return [aliases]
        if isinstance(aliases, list):
            return [str(a) for a in aliases if a]
        return []

    def _has_code_blocks(self, content: str) -> bool:
        """Check if content contains fenced code blocks."""
        # Match fenced code blocks: ```lang or ``` or ~~~
        pattern = r"^(?:```|~~~)"
        return bool(re.search(pattern, content, re.MULTILINE))

    def _extract_headings(self, content: str) -> List[Dict[str, Any]]:
        """
        Extract markdown headings with their levels and positions.

        Returns list of dicts with: level, text, start_pos, end_pos
        """
        headings = []
        # Match ATX-style headings: # Heading
        pattern = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$", re.MULTILINE)

        for match in pattern.finditer(content):
            level = len(match.group(1))
            text = match.group(2).strip()
            headings.append({
                "level": level,
                "text": text,
                "start_pos": match.start(),
                "end_pos": match.end(),
            })

        return headings

    def _build_heading_path(self, headings: List[Dict[str, Any]], position: int = 0) -> str:
        """
        Build a heading path (breadcrumb) for a given position in the document.

        Example: "Chapter 1 > Section A > Subsection"
        """
        if not headings:
            return ""

        # Find all headings before the given position
        relevant_headings = [h for h in headings if h["start_pos"] <= position]

        if not relevant_headings:
            return ""

        # Build path maintaining hierarchy
        path_parts = []
        current_level = 0

        for heading in relevant_headings:
            level = heading["level"]
            text = heading["text"]

            if level <= current_level:
                # Same or higher level - trim path back
                while path_parts and path_parts[-1][0] >= level:
                    path_parts.pop()

            path_parts.append((level, text))
            current_level = level

        return " > ".join(text for _, text in path_parts)

    def _extract_wikilinks(self, content: str) -> List[str]:
        """
        Extract wikilinks from markdown content.

        Examples:
            [[Note Name]]
            [[Note Name|Display Text]]
            [[Folder/Note Name]]
        """
        # Pattern for wikilinks: [[link]] or [[link|alias]]
        pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
        matches = re.findall(pattern, content)
        return matches

    def _build_link_map(self, md_files: List[Path]) -> Dict[str, str]:
        link_map = {}
        for md_file in md_files:
            try:
                relative_path = md_file.relative_to(self.vault_path)
                link_map[md_file.stem] = str(relative_path)
            except ValueError:
                continue
        return link_map

    def _resolve_wikilinks(self, wikilinks: List[str], link_map: Dict[str, str]) -> List[str]:
        resolved = []
        for link in wikilinks:
            key = Path(link).stem
            resolved.append(link_map.get(key, link))
        return resolved
