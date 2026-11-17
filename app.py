"""My Journal: creates the Flask app and wires the pieces together.

Settings come from config.py (environment variables / .env), each request
gets one database connection from db.py, and the pages live in blueprints
under views/:

    entries   /  /write  /entry/<id>  /edit/<id>  /favorite  /delete  /restore  /trash
    history   /history
    insights  /insights  /week
    backup    /backup  and its downloads and restore
"""
from datetime import date, datetime
import os
import threading

from flask import Flask, current_app, render_template, request

import db
from backup import run_daily_backup
from config import load_config
from database import initialize_database
from trash import cleanup_trash
from views import backup as backup_views
from views import entries, history, insights
from views.shared import register_template_helpers


# (database path, day) pairs whose daily backup has already been handled.
_daily_backups_checked = set()
_daily_backup_lock = threading.Lock()


def ensure_database_schema():
    """Upgrade the configured database before any route reads or writes it."""
    initialize_database(current_app.config["DATABASE"])


def ensure_daily_backup():
    """On the first request each day, save a dated copy to backups/,
    then erase entries that have been in Trash for more than 30 days.

    A failed backup is logged and never stops the page from loading. It is
    not retried until the next day (or a server restart), so a persistent
    problem doesn't slow down or spam every request.
    """
    if request.endpoint == "static":
        return
    database_path = current_app.config["DATABASE"]
    key = (os.path.abspath(database_path), date.today())
    if key in _daily_backups_checked:
        return
    with _daily_backup_lock:
        if key in _daily_backups_checked:
            return
        try:
            # Copies the database file, so it uses its own connections.
            run_daily_backup(database_path, key[1])
        except Exception:
            current_app.logger.exception("Automatic daily backup failed")
        # After the backup, so entries about to be erased are in today's copy.
        try:
            run_trash_cleanup()
        except Exception:
            current_app.logger.exception("Automatic trash cleanup failed")
        _daily_backups_checked.add(key)


def run_trash_cleanup():
    """Erase entries that have been in Trash for more than 30 days."""
    connection = db.get_db()
    with connection:
        return cleanup_trash(connection, datetime.now())


def page_not_found(error):
    return render_template("404.html"), 404


def init_db_command():
    """Add missing schema columns without replacing existing data."""
    initialize_database(current_app.config["DATABASE"])
    print("Database initialized successfully!")


def create_app(overrides=None):
    """Build the app. Raises config.ConfigError if the environment is unsafe."""
    app = Flask(__name__)
    app.config.update(load_config())
    if overrides:
        app.config.update(overrides)

    db.init_app(app)
    register_template_helpers(app)

    app.before_request(ensure_database_schema)
    app.before_request(ensure_daily_backup)

    app.register_blueprint(entries.bp)
    app.register_blueprint(history.bp)
    app.register_blueprint(insights.bp)
    app.register_blueprint(backup_views.bp)

    app.register_error_handler(404, page_not_found)
    app.cli.command("init-db")(init_db_command)
    return app


app = create_app()


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=app.config["APP_ENV"] == "development", port=5001)
