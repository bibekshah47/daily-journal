"""Connections to SQLite (local development, tests) or PostgreSQL (production).

Everything else in the app talks to the database through `connect()` and the
small Connection API below, with SQL written once using ? placeholders:

    connection.execute(sql, params)    -> cursor (fetchone, fetchall, rowcount)
    connection.fetchone(sql, params)   -> one row or None
    connection.fetchall(sql, params)   -> list of rows
    connection.insert(sql, params)     -> the new row's id (adds RETURNING id)
    with connection: ...               -> commit, or roll back on an error

Rows work like tuples (row[0], unpacking, comparing to a tuple) and by column
name (row["title"], dict(row)), the same on both databases.

A "target" is either a SQLite file path or a postgres:// / postgresql:// URL.
Schema setup and upgrades live in migrations.py.
"""
import functools
import sqlite3
import weakref

try:
    import psycopg
except ImportError:   # only needed when a PostgreSQL URL is configured
    psycopg = None

POSTGRES_SCHEMES = ("postgres://", "postgresql://")
CONNECT_TIMEOUT_SECONDS = 10

# Raised for a UNIQUE or foreign key violation on either database.
IntegrityError = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())

# Open connections, so test cleanup can close stragglers before dropping a schema.
_open_connections = weakref.WeakSet()


def is_postgres(target):
    return str(target or "").startswith(POSTGRES_SCHEMES)


# ---------- Placeholders ----------

@functools.lru_cache(maxsize=512)
def to_postgres_sql(sql):
    """Rewrite ? placeholders as %s for psycopg.

    A ? inside a quoted string ('...' or "...") or a -- comment is left alone,
    and every literal % is doubled so psycopg doesn't read it as a placeholder.
    """
    out = []
    quote = None
    in_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "%":
            out.append("%%")
        elif in_comment:
            out.append(ch)
            in_comment = ch != "\n"
        elif quote:
            out.append(ch)
            if ch == quote:   # a doubled quote ('') closes and reopens: still correct
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "-" and sql[i + 1:i + 2] == "-":
            in_comment = True
            out.append("--")
            i += 1
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---------- Rows ----------

@functools.lru_cache(maxsize=256)
def _row_class(names):
    index = {name: position for position, name in enumerate(names)}

    class Row(tuple):
        """A tuple whose values can also be read by column name."""
        __slots__ = ()

        def __getitem__(self, key):
            if isinstance(key, str):
                return tuple.__getitem__(self, index[key])
            return tuple.__getitem__(self, key)

        def keys(self):
            return list(names)

    return Row


def _sqlite_row(cursor, values):
    return _row_class(tuple(column[0] for column in cursor.description))(values)


def _postgres_row_factory(cursor):
    names = tuple(column.name for column in cursor.description or ())
    return _row_class(names)


# ---------- Connections ----------

class Cursor:
    """The result of Connection.execute()."""

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor.fetchall() if self._cursor.description else ())

    @property
    def rowcount(self):
        return self._cursor.rowcount


class Connection:
    def __init__(self, raw, backend):
        self.raw = raw
        self.backend = backend
        _open_connections.add(self)

    @property
    def is_postgres(self):
        return self.backend == "postgres"

    def execute(self, sql, params=()):
        params = tuple(params)
        if self.is_postgres:
            return Cursor(self.raw.execute(to_postgres_sql(sql), params))
        return Cursor(self.raw.execute(sql, params))

    def fetchone(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def executemany(self, sql, rows):
        """Run one statement for many parameter rows (bulk copies)."""
        rows = [tuple(row) for row in rows]
        if not rows:
            return
        if self.is_postgres:
            with self.raw.cursor() as cursor:
                cursor.executemany(to_postgres_sql(sql), rows)
        else:
            self.raw.executemany(sql, rows)

    def insert(self, sql, params=()):
        """Run an INSERT of one row and return its id (RETURNING id works on
        SQLite 3.35+ and PostgreSQL)."""
        return self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params).fetchone()[0]

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        _open_connections.discard(self)
        self.raw.close()

    # Like sqlite3 (and unlike psycopg): the block is one transaction, and the
    # connection stays open afterwards.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def connect(target):
    """Open a connection to a SQLite file or a PostgreSQL URL."""
    if is_postgres(target):
        if psycopg is None:
            raise RuntimeError('PostgreSQL needs psycopg: pip install "psycopg[binary]"')
        # prepare_threshold=None: no server-side prepared statements, which a
        # transaction pooler (like Neon's "-pooler" endpoint) can't keep track
        # of. Connections only last one request, so they'd gain nothing anyway.
        raw = psycopg.connect(str(target), connect_timeout=CONNECT_TIMEOUT_SECONDS,
                              row_factory=_postgres_row_factory, prepare_threshold=None)
        return Connection(raw, "postgres")
    raw = sqlite3.connect(target)
    raw.row_factory = _sqlite_row
    # SQLite leaves foreign keys off unless asked, per connection; with them
    # on, deleting an entry or account also deletes what belongs to it.
    raw.execute("PRAGMA foreign_keys = ON")
    return Connection(raw, "sqlite")


def close_open_connections():
    """Close every connection still open (used by test cleanup)."""
    for connection in list(_open_connections):
        try:
            connection.close()
        except Exception:
            pass


def initialize_database(target):
    """Create or upgrade the schema (runs pending migrations). Returns the
    versions that were applied."""
    from migrations import migrate
    return migrate(target)


if __name__ == "__main__":
    from config import load_config
    applied = initialize_database(load_config()["DATABASE"])
    print(f"Database ready. Applied migrations: {applied or 'none'}")
