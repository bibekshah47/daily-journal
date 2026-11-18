"""Copy a SQLite journal into an empty PostgreSQL database.

Used by `flask --app app copy-sqlite-to-postgres --sqlite journal.db`.
Users, entries, tags and tag links are copied with their ids, in one
transaction, then each identity sequence is moved past the highest id so new
rows never collide with copied ones.

The SQLite file itself is never changed: it is copied to a temporary file,
which is upgraded to the current schema (in case it is an older journal) and
read from there.
"""
from pathlib import Path
import tempfile

from backup import copy_database
from database import connect, is_postgres
from migrations import migrate

# Parents before children, so foreign keys are satisfied as rows arrive.
TABLES = (
    ("users", ("id", "email", "password_hash", "created_at", "display_name",
               "session_version", "password_changed_at", "timezone")),
    ("entries", ("id", "content", "created_at", "mood", "content_html", "title",
                 "is_favorite", "went_well", "went_wrong", "improve_tomorrow",
                 "deleted_at", "user_id")),
    ("tags", ("id", "user_id", "name")),
    ("entry_tags", ("entry_id", "tag_id", "sort_order")),
)
IDENTITY_TABLES = ("users", "entries", "tags")


class CopyError(RuntimeError):
    """The copy can't be done; nothing was written."""


def copy_sqlite_to_postgres(sqlite_path, target):
    """Copy everything from the SQLite file into `target`. Returns
    {table: rows copied}. Raises CopyError if the target isn't an empty
    PostgreSQL database."""
    if not is_postgres(target):
        raise CopyError("The configured database isn't PostgreSQL. Set DATABASE_URL "
                        "to the PostgreSQL connection string first.")
    if not Path(sqlite_path).is_file():
        raise CopyError(f"No SQLite file at {sqlite_path}.")

    migrate(target)
    with tempfile.TemporaryDirectory() as folder:
        snapshot = str(Path(folder) / "journal.db")
        copy_database(sqlite_path, snapshot)
        migrate(snapshot)
        source = connect(snapshot)
        destination = connect(target)
        try:
            if (destination.fetchone("SELECT COUNT(*) FROM users")[0]
                    or destination.fetchone("SELECT COUNT(*) FROM entries")[0]):
                raise CopyError("The PostgreSQL database already has users or entries. "
                                "Copy only into an empty database.")
            counts = {}
            for table, columns in TABLES:
                names = ", ".join(columns)
                rows = source.fetchall(f"SELECT {names} FROM {table}")
                destination.executemany(
                    f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' for _ in columns)})",
                    rows)
                counts[table] = len(rows)
            for table in IDENTITY_TABLES:
                # is_called = false: the next id handed out is exactly MAX(id) + 1.
                destination.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
            destination.commit()
            return counts
        except Exception:
            destination.rollback()
            raise
        finally:
            source.close()
            destination.close()
