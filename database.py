"""Create and upgrade the journal schema without replacing existing entries."""
import sqlite3
import os


def initialize_database(database_path="journal.db"):
    connection = sqlite3.connect(database_path)
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
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE entries ADD COLUMN {name} {definition}"
                    )
    finally:
        connection.close()


if __name__ == "__main__":
    initialize_database(os.environ.get("JOURNAL_DATABASE", "journal.db"))
    print("Database initialized successfully!")
