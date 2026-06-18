"""Tests for adapter state enumeration (index-ledger P1).

Each source reports its current indexable units + an opaque per-unit
fingerprint; the reconciliation planner diffs these against the register.
See docs/SPEC_REGISTER_AS_INDEX_LEDGER.md.
"""

import sqlite3

from src.sources.obsidian import ObsidianSource
from src.sources.zotero import ZoteroSource


def _zotero_db(tmp_path):
    """A Zotero-shaped fixture with the columns enumerate_state reads."""
    data_dir = tmp_path / "Zotero"
    storage = data_dir / "storage"
    storage.mkdir(parents=True)
    db_path = data_dir / "zotero.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INTEGER,
                            dateAdded TEXT, dateModified TEXT, key TEXT);
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
        CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,
                                      path TEXT, contentType TEXT,
                                      storageModTime INTEGER, storageHash TEXT);
        CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INTEGER, note TEXT);
        CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,
                                      text TEXT, comment TEXT);
        INSERT INTO itemTypes VALUES (1,'book'),(2,'attachment'),(3,'annotation'),(4,'note');

        -- Parent P1: hashed attachment + note + annotation
        INSERT INTO items VALUES (10, 1, '', '2026-01-01 00:00:00', 'P1');
        INSERT INTO items VALUES (20, 2, '', '2026-01-01 00:00:00', 'ATT_HASH');
        INSERT INTO items VALUES (30, 3, '', '2026-01-01 00:00:00', 'ANN1');
        INSERT INTO items VALUES (40, 4, '', '2026-01-02 00:00:00', 'NOTE1');
        INSERT INTO itemAttachments VALUES (20, 10, 'storage:doc.pdf', 'application/pdf', 111, 'deadbeef');
        INSERT INTO itemAnnotations VALUES (30, 20, 'hi', 'cmt');
        INSERT INTO itemNotes VALUES (40, 10, '<p>n</p>');

        -- Parent P2: no-hash attachment (mtime fallback) + a no-file attachment (omitted)
        INSERT INTO items VALUES (11, 1, '', '2026-01-03 00:00:00', 'P2');
        INSERT INTO items VALUES (21, 2, '', '2026-01-03 00:00:00', 'ATT_FILE');
        INSERT INTO items VALUES (22, 2, '', '2026-01-03 00:00:00', 'ATT_MISSING');
        INSERT INTO itemAttachments VALUES (21, 11, 'storage:present.pdf', 'application/pdf', NULL, NULL);
        INSERT INTO itemAttachments VALUES (22, 11, 'storage:missing.pdf', 'application/pdf', NULL, NULL);

        -- Deleted parent P3 (must not appear)
        INSERT INTO items VALUES (12, 1, '', '2026-01-04 00:00:00', 'P3');
        INSERT INTO deletedItems VALUES (12, '2026-01-05 00:00:00');
        """
    )
    conn.commit()
    conn.close()

    # Resolvable file for the mtime-fallback attachment (ATT_FILE).
    f = storage / "ATT_FILE" / "present.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"hello world")
    return data_dir


def _source(data_dir):
    return ZoteroSource(
        {"zotero": {"enabled": True, "data_directory": str(data_dir)}}
    )


def test_zotero_enumerates_all_unit_kinds_rolled_up_to_parent(tmp_path):
    units = _source(_zotero_db(tmp_path)).enumerate_state()

    # P1: meta + attachment + annotation + note ; P2: meta + one resolvable attachment
    assert set(units) == {
        "zotero:P1:meta",
        "zotero:P1:attachment:ATT_HASH",
        "zotero:P1:annotation:ANN1",
        "zotero:P1:note:NOTE1",
        "zotero:P2:meta",
        "zotero:P2:attachment:ATT_FILE",
    }
    # Identity always rolls up to the parent zotero_key.
    assert units["zotero:P1:note:NOTE1"].identity_value == "P1"
    assert units["zotero:P1:note:NOTE1"].identity_field == "zotero_key"
    # Deleted parent and the no-file attachment are absent.
    assert not any(u.identity_value == "P3" for u in units.values())
    assert "zotero:P2:attachment:ATT_MISSING" not in units


def test_zotero_attachment_fingerprint_is_composite(tmp_path):
    units = _source(_zotero_db(tmp_path)).enumerate_state()
    assert units["zotero:P1:attachment:ATT_HASH"].fingerprint == "hash:deadbeef"
    assert units["zotero:P2:attachment:ATT_FILE"].fingerprint.startswith("mtime:")


def test_zotero_touching_a_note_changes_exactly_one_fingerprint(tmp_path):
    data_dir = _zotero_db(tmp_path)
    before = _source(data_dir).enumerate_state()

    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    conn.execute("UPDATE items SET dateModified='2026-06-17 12:00:00' WHERE key='NOTE1'")
    conn.commit()
    conn.close()

    after = _source(data_dir).enumerate_state()
    assert set(before) == set(after)
    changed = [uid for uid in before if before[uid].fingerprint != after[uid].fingerprint]
    assert changed == ["zotero:P1:note:NOTE1"]


def test_obsidian_enumerates_vault_files(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Concepts").mkdir(parents=True)
    (vault / "Concepts" / "a.md").write_text("alpha")
    source = ObsidianSource(
        {"obsidian": {"enabled": True, "vault_path": str(vault),
                      "include_folders": ["Concepts"]}}
    )
    units = source.enumerate_state()
    unit_id = "obsidian:Concepts/a.md"
    assert set(units) == {unit_id}
    assert units[unit_id].identity_field == "source_id"
    assert units[unit_id].identity_value == "obsidian-Concepts/a.md"
    assert units[unit_id].unit_kind == "vault_file"

    before_fp = units[unit_id].fingerprint
    (vault / "Concepts" / "a.md").write_text("alpha plus more content")
    after = source.enumerate_state()
    assert after[unit_id].fingerprint != before_fp
