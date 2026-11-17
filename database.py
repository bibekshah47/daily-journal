"""Create and upgrade the journal schema without replacing existing entries."""
import sqlite3


def connect(database_path):
    """Open a connection with foreign keys enforced (SQLite leaves them off).

    Every connection the app opens goes through here, so deleting an entry
    also deletes its tag links.
    """
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path="journal.db"):
    connection = connect(database_path)
    try:
        with connection:
            # Serialize schema checks and upgrades across concurrent requests.
            # Explicit BEGIN also keeps the DDL changes in one transaction.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    mood TEXT
                )
            """)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(entries)")
            }
            # Fixed names only: never interpolate user input into schema SQL.
            additions = {
                "content_html": "TEXT",
                "title": "TEXT",
                "is_favorite": "INTEGER NOT NULL DEFAULT 0",
                "went_well": "TEXT",
                "went_wrong": "TEXT",
                "improve_tomorrow": "TEXT",
                # When the entry was moved to Trash; NULL means not deleted.
                "deleted_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE entries ADD COLUMN {name} {definition}"
                    )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS entry_tags (
                    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (entry_id, tag_id)
                )
            """)
            # Everything the app shows reads from this view, so trashed
            # entries are hidden in one place. SQLite expands "*" when the
            # view is used, so columns added later appear here too.
            connection.execute(
                "CREATE VIEW IF NOT EXISTS live_entries AS "
                "SELECT * FROM entries WHERE deleted_at IS NULL"
            )
            # The History tag filter looks entries up by tag.
            connection.execute(
                "CREATE INDEX IF NOT EXISTS entry_tags_by_tag ON entry_tags (tag_id)"
            )
    finally:
        connection.close()


if __name__ == "__main__":
    from config import database_path
    initialize_database(database_path())
    print("Database initialized successfully!")
