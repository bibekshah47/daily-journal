"""One database connection per request.

Routes call get_db(); the connection is opened on first use, shared for the
rest of the request and closed when the request ends. Helper modules never
open connections: they take one as an argument. The only exceptions are the
schema setup in database.py and the file copies in backup.py.
"""
import sqlite3

from flask import current_app, g

from database import connect


def get_db():
    """This request's connection: rows as sqlite3.Row, foreign keys on."""
    if "db" not in g:
        connection = connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        g.db = connection
    return g.db


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
