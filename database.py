"""Create and upgrade the journal schema without replacing existing entries."""
import sqlite3


def initialize_database(database_path="journal.db"):
    connection = sqlite3.connect(database_path)
    try:
        with connection:
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
    initialize_database()
    print("Database initialized successfully!")
