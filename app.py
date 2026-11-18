"""My Journal: creates the Flask app and wires the pieces together.

Settings come from config.py (environment variables / .env). The database
is SQLite locally and PostgreSQL in production (database.py); its schema is
brought up to date by migrations.py when the app starts. Each request gets
one database connection from db.py, and the pages live in blueprints under
views/:

    auth      /signup  /login  /logout  /forgot  /reset/<token>
              (the only pages open without logging in)
    entries   /  /write  /entry/<id>  /edit/<id>  /favorite  /delete  /restore  /trash
    history   /history
    insights  /insights  /week
    backup    /backup  and its downloads and restore
    settings  /settings: name, email, password, delete account
"""
import os
import sys
import threading

import click
from flask import Flask, current_app, render_template, request

import db
import security
from accounts import assign_unowned, find_user_by_email
from backup import run_daily_backup
from config import load_config
from copy_to_postgres import CopyError, copy_sqlite_to_postgres
from database import initialize_database, is_postgres
from timeutil import utc_now, utc_today
from trash import cleanup_trash
from views import backup as backup_views
from views import auth, entries, history, insights, settings
from views.shared import register_template_helpers


# (database, day) pairs whose daily maintenance has already been handled.
_daily_backups_checked = set()
_daily_backup_lock = threading.Lock()


def maintenance_key(database, day=None):
    """Identifies one database's daily maintenance in _daily_backups_checked.
    Maintenance is system work, so "daily" means the UTC day."""
    return (database if is_postgres(database) else os.path.abspath(database),
            day or utc_today())


def ensure_daily_backup():
    """On the first request each day, save a dated copy to backups/ (SQLite
    only), then erase entries that have been in Trash for more than 30 days.

    PostgreSQL gets no file copy: the database host keeps its own restore
    history (Neon does). A failed backup is logged and never stops the page
    from loading. It is not retried until the next day (or a server
    restart), so a persistent problem doesn't slow down or spam every request.
    """
    if request.endpoint == "static":
        return
    database = current_app.config["DATABASE"]
    postgres = is_postgres(database)
    key = maintenance_key(database)
    if key in _daily_backups_checked:
        return
    with _daily_backup_lock:
        if key in _daily_backups_checked:
            return
        if not postgres:
            try:
                # Copies the database file, so it uses its own connections.
                run_daily_backup(database, key[1])
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
        return cleanup_trash(connection, utc_now())


def page_not_found(error):
    return render_template("404.html"), 404


def init_db_command():
    """Run any pending schema migrations on the configured database."""
    applied = initialize_database(current_app.config["DATABASE"])
    kind = "PostgreSQL" if is_postgres(current_app.config["DATABASE"]) else "SQLite"
    if applied:
        click.echo(f"{kind} database upgraded: applied migration(s) "
                   f"{', '.join(str(version) for version in applied)}.")
    else:
        click.echo(f"{kind} database is up to date.")


@click.option("--sqlite", "sqlite_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="The SQLite journal file to copy from (it is not changed).")
def copy_sqlite_command(sqlite_path):
    """Copy users, entries and tags from a SQLite file into the configured,
    empty PostgreSQL database (DATABASE_URL), keeping their ids."""
    try:
        counts = copy_sqlite_to_postgres(sqlite_path, current_app.config["DATABASE"])
    except CopyError as error:
        raise click.ClickException(str(error)) from None
    click.echo("Copied " + ", ".join(f"{count} {table}" for table, count in counts.items()) + ".")


@click.option("--email", required=True, help="Email of the existing account to receive the entries.")
def assign_entries_command(email):
    """Give every entry and tag without an owner to an existing account."""
    connection = db.get_db()
    user = find_user_by_email(connection, email)
    if user is None:
        raise click.ClickException(f"No account uses {email.strip()}. Sign up first, then run this again.")
    moved_entries, moved_tags = assign_unowned(connection, user[0])
    click.echo(f"Assigned {moved_entries} {'entry' if moved_entries == 1 else 'entries'} and "
               f"{moved_tags} {'tag' if moved_tags == 1 else 'tags'} to {user[1]}.")


def create_app(overrides=None):
    """Build the app. Raises config.ConfigError if the environment is unsafe."""
    app = Flask(__name__)
    app.config.update(load_config())
    if overrides:
        app.config.update(overrides)

    # Bring the schema up to date once, at startup (not on every request).
    applied = initialize_database(app.config["DATABASE"])
    if applied:
        print(f"Applied database migration(s) {', '.join(map(str, applied))} at startup.",
              file=sys.stderr)

    db.init_app(app)
    register_template_helpers(app)

    app.before_request(ensure_daily_backup)
    auth.init_app(app)
    # After the login check, so a logged-out post goes to /login, not the CSRF page.
    security.init_app(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(entries.bp)
    app.register_blueprint(history.bp)
    app.register_blueprint(insights.bp)
    app.register_blueprint(backup_views.bp)
    app.register_blueprint(settings.bp)

    app.register_error_handler(404, page_not_found)
    app.cli.command("init-db")(init_db_command)
    app.cli.command("assign-entries")(assign_entries_command)
    app.cli.command("copy-sqlite-to-postgres")(copy_sqlite_command)
    return app


app = create_app()


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=app.config["APP_ENV"] == "development", port=5001)
