# zotero.py

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_db_connection(zotero_db: Path) -> Optional[sqlite3.Connection]:
    """Establishes a read-only connection to the Zotero SQLite database."""
    if not zotero_db.exists():
        print(f"Zotero database not found at: {zotero_db}")
        return None
    try:
        # Connect in read-only mode to prevent accidental writes
        uri = f"{zotero_db.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        return None


def get_all_items(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Retrieves all non-deleted items from the database."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT itemID, itemTypeID, dateAdded, dateModified FROM items WHERE itemID NOT IN (SELECT itemID FROM deletedItems)"
    )
    return cursor.fetchall()


def get_item_data_values(conn: sqlite3.Connection, item_id: int) -> Dict[str, Any]:
    """Retrieves all metadata for a single item."""
    cursor = conn.cursor()
    query = """
    SELECT f.fieldName, v.value
    FROM itemData id
    JOIN itemDataValues v ON id.valueID = v.valueID
    JOIN fields f ON id.fieldID = f.fieldID
    WHERE id.itemID = ?
    """
    cursor.execute(query, (item_id,))
    return {row["fieldName"]: row["value"] for row in cursor.fetchall()}


def get_item_creators(conn: sqlite3.Connection, item_id: int) -> List[str]:
    """Retrieves all creators for a single item."""
    cursor = conn.cursor()
    query = """
    SELECT c.firstName, c.lastName
    FROM itemCreators ic
    JOIN creators c ON ic.creatorID = c.creatorID
    WHERE ic.itemID = ?
    ORDER BY ic.orderIndex
    """
    cursor.execute(query, (item_id,))
    return [
        f"{row['firstName']} {row['lastName']}".strip() for row in cursor.fetchall()
    ]


def get_item_tags(conn: sqlite3.Connection, item_id: int) -> List[str]:
    """Retrieves all tags for a single item."""
    cursor = conn.cursor()
    query = "SELECT t.name FROM itemTags it JOIN tags t ON it.tagID = t.tagID WHERE it.itemID = ?"
    cursor.execute(query, (item_id,))
    return [row["name"] for row in cursor.fetchall()]


def get_item_collections(conn: sqlite3.Connection, item_id: int) -> List[str]:
    """Retrieves all collections for a single item."""
    cursor = conn.cursor()
    query = "SELECT c.collectionName FROM collectionItems ci JOIN collections c ON ci.collectionID = c.collectionID WHERE ci.itemID = ?"
    cursor.execute(query, (item_id,))
    return [row["collectionName"] for row in cursor.fetchall()]


def get_item_attachments(
    conn: sqlite3.Connection, item_id: int, zotero_storage_dir: Path
) -> List[Dict[str, Any]]:
    """Retrieves all attachments for a single item."""
    cursor = conn.cursor()
    query = "SELECT path, contentType FROM itemAttachments WHERE parentItemID = ? AND path IS NOT NULL"
    cursor.execute(query, (item_id,))
    attachments = []
    for row in cursor.fetchall():
        # Path is relative to the storage directory, e.g., 'storage:FILENAME.pdf'
        if row["path"] and row["path"].startswith("storage:"):
            filename = row["path"].split(":")[1]
            # The actual files are in subdirectories named with the item's key.
            # This requires another lookup to get the item key.
            key_cursor = conn.cursor()
            key_cursor.execute("SELECT key FROM items WHERE itemID = ?", (item_id,))
            item_key_row = key_cursor.fetchone()
            if item_key_row:
                item_key = item_key_row["key"]
                attachments.append(
                    {
                        "path": zotero_storage_dir / item_key / filename,
                        "contentType": row["contentType"],
                    }
                )
    return attachments


def get_item_notes(conn: sqlite3.Connection, item_id: int) -> List[str]:
    """Retrieves all notes for a single item."""
    cursor = conn.cursor()
    query = "SELECT note FROM itemNotes WHERE parentItemID = ?"
    cursor.execute(query, (item_id,))
    return [row["note"] for row in cursor.fetchall()]


def get_single_zotero_item(
    conn: sqlite3.Connection, item_id: int, zotero_storage_dir: Path
) -> Optional[Dict[str, Any]]:
    """Retrieves structured data for a single Zotero item."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT itemID, itemTypeID, dateAdded, dateModified FROM items WHERE itemID = ? AND itemID NOT IN (SELECT itemID FROM deletedItems)",
        (item_id,),
    )
    item_row = cursor.fetchone()

    if not item_row:
        return None

    return {
        "id": f"zotero-{item_id}",
        "zotero_id": item_id,
        "item_type_id": item_row["itemTypeID"],
        "date_added": item_row["dateAdded"],
        "date_modified": item_row["dateModified"],
        "metadata": get_item_data_values(conn, item_id),
        "creators": get_item_creators(conn, item_id),
        "tags": get_item_tags(conn, item_id),
        "collections": get_item_collections(conn, item_id),
        "attachments": get_item_attachments(conn, item_id, zotero_storage_dir),
        "notes": get_item_notes(conn, item_id),
    }


def get_zotero_data(zotero_db: Path, zotero_storage_dir: Path) -> List[Dict[str, Any]]:
    """
    Connects to the Zotero SQLite database and extracts structured data for all items.
    """
    conn = get_db_connection(zotero_db)
    if not conn:
        return []

    all_items_data = []
    items = get_all_items(conn)

    for item_row in items:
        item_id = item_row["itemID"]
        item_data = get_single_zotero_item(conn, item_id, zotero_storage_dir)
        if item_data:
            all_items_data.append(item_data)

    conn.close()
    return all_items_data


if __name__ == "__main__":
    # --- Configuration ---
    # IMPORTANT: Update these paths to match your Zotero installation.
    # On Windows, this is typically in C:\\Users\\<YourUser>\\Zotero
    # On macOS, this is typically in /Users/<YourUser>/Zotero
    # On Linux, this is typically in /home/<YourUser>/Zotero
    ZOTERO_DATA_DIR = Path.home() / "Zotero"
    ZOTERO_DB_PATH = ZOTERO_DATA_DIR / "zotero.sqlite"
    ZOTERO_STORAGE_DIR = ZOTERO_DATA_DIR / "storage"
    OUTPUT_FILE = Path(__file__).parent.parent / "zotero_library.json"

    # --- Main Execution ---
    print(f"Connecting to Zotero database at: {ZOTERO_DB_PATH}")
    zotero_library = get_zotero_data(ZOTERO_DB_PATH, ZOTERO_STORAGE_DIR)

    if zotero_library:
        print(f"Successfully extracted data for {len(zotero_library)} items.")

        # Save the extracted data to a JSON file for inspection
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                zotero_library, f, indent=2, default=str
            )  # Use default=str for Path objects
        print(f"Full library data saved to: {OUTPUT_FILE}")

        # Print a summary of the first 5 items
        print("\n--- Sample of extracted data (first 5 items) ---")
        for item in zotero_library[:5]:
            print(json.dumps(item, indent=2, default=str))
            print("-" * 20)
    else:
        print("Could not extract any data from the Zotero library.")
        print("Please check the path to your 'zotero.sqlite' file in this script.")
