
import sqlite3

connection = sqlite3.connect("journal.db")

cursor = connection.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""")

connection.commit()
connection.close()

print("Database created successfully!")
