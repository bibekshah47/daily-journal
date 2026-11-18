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


# Tags belong to one user: the same name can exist once per user.
TAGS_TABLE = """
    CREATE TABLE IF NOT EXISTS {name} (
        id INTEGER PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        UNIQUE (user_id, name)
    )
"""


# AUTOINCREMENT so a deleted account's id is never given to a new account:
# logins ("<id>:<session_version>") and reset links name users by id, so a
# reused id could let a stale login or link reach someone else's account.
USERS_TABLE = """
    CREATE TABLE IF NOT EXISTS {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        display_name TEXT,
        session_version INTEGER NOT NULL DEFAULT 0,
        password_changed_at TEXT
    )
"""
USER_COLUMNS = ("id, email, password_hash, created_at, display_name, session_version, "
                "password_changed_at")


def _columns(connection, table):
    # Fixed table names only.
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _tags_need_rebuild(connection):
    """True for a tags table from before accounts (global UNIQUE(name))."""
    columns = _columns(connection, "tags")
    return bool(columns) and "user_id" not in columns


def _rebuild_tags_per_user(connection):
    """Swap the global UNIQUE(name) for UNIQUE(user_id, name).

    SQLite can't change a constraint in place, so this copies the table,
    keeping every id so entry_tags still points at the right tags. Existing
    tags get user_id NULL, like existing entries, until they are claimed
    with `flask assign-entries`. Runs inside the caller's transaction, with
    foreign keys off (otherwise DROP TABLE would delete every entry_tags row).
    """
    links_before = connection.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0]
    tags_before = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    connection.execute(TAGS_TABLE.format(name="tags_per_user"))
    connection.execute(
        "INSERT INTO tags_per_user (id, user_id, name) SELECT id, NULL, name FROM tags")
    connection.execute("DROP TABLE tags")
    connection.execute("ALTER TABLE tags_per_user RENAME TO tags")
    links_after = connection.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0]
    tags_after = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    if (links_before, tags_before) != (links_after, tags_after):
        raise RuntimeError("Tag upgrade lost rows; nothing was changed.")


def _users_need_rebuild(connection):
    """True for a users table whose ids can be reused (no AUTOINCREMENT)."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
    return row is not None and "AUTOINCREMENT" not in row[0].upper()


def _rebuild_users_autoincrement(connection):
    """Copy users into an AUTOINCREMENT table, keeping every id (and so every
    entry and tag owner). Runs inside the caller's transaction with foreign
    keys off, like the tags rebuild: otherwise DROP TABLE would cascade."""
    before = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    connection.execute(USERS_TABLE.format(name="users_autoincrement"))
    connection.execute(f"INSERT INTO users_autoincrement ({USER_COLUMNS}) "
                       f"SELECT {USER_COLUMNS} FROM users")
    connection.execute("DROP TABLE users")
    connection.execute("ALTER TABLE users_autoincrement RENAME TO users")
    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] != before:
        raise RuntimeError("User upgrade lost rows; nothing was changed.")


def initialize_database(database_path="journal.db"):
    connection = connect(database_path)
    try:
        # The tags and users rebuilds must run with foreign keys off, and
        # SQLite only lets that change outside a transaction. Only this
        # connection is affected, and it is closed at the end.
        if _tags_need_rebuild(connection) or _users_need_rebuild(connection):
            connection.execute("PRAGMA foreign_keys = OFF")
        with connection:
            # Serialize schema checks and upgrades across concurrent requests.
            # Explicit BEGIN also keeps the DDL changes in one transaction.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(USERS_TABLE.format(name="users"))
            # Optional name for the greeting; NULL for existing and unnamed users.
            if "display_name" not in _columns(connection, "users"):
                connection.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
            # Raised to log the user out everywhere (password change or reset).
            if "session_version" not in _columns(connection, "users"):
                connection.execute(
                    "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
            # Shown on Settings; NULL (not shown) until the password next changes.
            if "password_changed_at" not in _columns(connection, "users"):
                connection.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT")
            # Checked inside the transaction, after the columns above exist.
            if _users_need_rebuild(connection):
                _rebuild_users_autoincrement(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    mood TEXT
                )
            """)
            columns = _columns(connection, "entries")
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
                # The owner. NULL for entries from before accounts, which no
                # one can see until `flask assign-entries` gives them an owner.
                "user_id": "INTEGER REFERENCES users(id) ON DELETE CASCADE",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE entries ADD COLUMN {name} {definition}"
                    )
            # Checked again inside the transaction: another process may have
            # upgraded the table while this one waited for the lock.
            if _tags_need_rebuild(connection):
                _rebuild_tags_per_user(connection)
            else:
                connection.execute(TAGS_TABLE.format(name="tags"))
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
            # Every page lists one user's entries by date.
            connection.execute(
                "CREATE INDEX IF NOT EXISTS entries_by_user ON entries (user_id, created_at)"
            )
            # Failed logins per email (keyed by a keyed hash, never the email
            # itself), so repeated guessing locks that email for a while.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS login_throttle (
                    email_hash TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL DEFAULT 0,
                    window_start TEXT,
                    locked_until TEXT
                )
            """)
    finally:
        connection.close()


if __name__ == "__main__":
    from config import database_path
    initialize_database(database_path())
    print("Database initialized successfully!")
