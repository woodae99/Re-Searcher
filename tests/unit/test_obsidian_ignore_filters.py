import json

from src.sources.obsidian import ObsidianSource


def test_obsidian_user_ignore_filters_are_applied(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text(
        json.dumps(
            {
                "userIgnoreFilters": [
                    ".smart",
                    "SystemSculpt/Chats/",
                    "Resources/chat *.md",
                ]
            }
        ),
        encoding="utf-8",
    )

    paths = [
        "Keep/root.md",
        ".smart/cache.md",
        "SystemSculpt/Chats/session.md",
        "Resources/chat export.md",
        "Resources/ordinary.md",
        "Templates/template.md",
    ]
    for path in paths:
        note_path = vault / path
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Note\n\nBody", encoding="utf-8")

    source = ObsidianSource(
        {
            "obsidian": {
                "enabled": True,
                "vault_path": str(vault),
                "include_folders": [""],
                "exclude_folders": ["Templates"],
                "use_obsidian_ignore_filters": True,
            }
        }
    )

    discovered = {
        path.relative_to(vault).as_posix()
        for path in source._find_markdown_files(
            source._normalize_folder_list(source.obsidian_config.get("include_folders")),
            source._get_exclude_patterns(),
        )
    }

    assert discovered == {"Keep/root.md", "Resources/ordinary.md"}


def test_empty_include_folders_scans_whole_vault_except_excludes(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()

    for path in ["A/one.md", "B/two.md", "Templates/template.md"]:
        note_path = vault / path
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Note\n\nBody", encoding="utf-8")

    source = ObsidianSource(
        {
            "obsidian": {
                "enabled": True,
                "vault_path": str(vault),
                "include_folders": [],
                "exclude_folders": ["Templates"],
            }
        }
    )

    discovered = {
        path.relative_to(vault).as_posix()
        for path in source._find_markdown_files(
            source._normalize_folder_list(source.obsidian_config.get("include_folders")),
            source._get_exclude_patterns(),
        )
    }

    assert discovered == {"A/one.md", "B/two.md"}
