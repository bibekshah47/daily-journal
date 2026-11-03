
# Import the SQLite library
import sqlite3

# Connect to the journal database
connection = sqlite3.connect("journal.db")

# Create a cursor to execute SQL commands
cursor = connection.cursor()

# Create the entries table if it does not exist
cursor.execute("""
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        mood TEXT
    )
""")

# Save database changes
connection.commit()

# Close the connection
connection.close()

print("Database created successfully!")
