"""One database connection per request.

Routes call get_db(); the connection (SQLite or PostgreSQL, see database.py)
is opened on first use, shared for the rest of the request and closed when
the request ends. Helper modules never open connections: they take one as an
argument. The exceptions are migrations.py, the SQLite file copies in
backup.py, and the copy-sqlite-to-postgres command.
"""
from flask import current_app, g

from database import connect, is_postgres


def get_db():
    """This request's connection. Rows work by index and by column name."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def uses_postgres(app=None):
    return is_postgres((app or current_app).config["DATABASE"])


def close_db(exception=None):
    """Close the request's connection, if one was opened.

    Anything not committed is rolled back by closing, so a failed request
    never leaves half a change behind.
    """
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_app(app):
    app.teardown_appcontext(close_db)
