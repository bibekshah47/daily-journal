"""Backup & export: downloads, and restoring a JSON backup."""
from datetime import datetime
import io

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from werkzeug.exceptions import RequestEntityTooLarge

from backup import (RestoreError, backup_folder, database_bytes, entries_to_json,
                    entries_to_markdown, latest_backup_time, parse_backup,
                    restore_entries, restore_message)
from config import MAX_UPLOAD_MB
from db import get_db
from tags import get_tags_for_entries

bp = Blueprint("backup", __name__)


def render_backup_page(error=None, status=200):
    database_path = current_app.config["DATABASE"]
    total, oldest, newest = get_db().execute(
        "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM live_entries"
    ).fetchone()
    try:
        last_backup = latest_backup_time(database_path)
    except OSError:
        last_backup = None
    return render_template(
        "backup.html",
        error=error,
        total_entries=total,
        oldest_entry=oldest,
        newest_entry=newest,
        last_backup=last_backup,
        backup_folder=backup_folder(database_path),
        max_upload_mb=MAX_UPLOAD_MB,
    ), status


def all_entries_newest_first():
    """Every entry not in Trash, newest first, plus {entry_id: [tag names]}."""
    connection = get_db()
    rows = connection.execute(
        "SELECT * FROM live_entries ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return rows, get_tags_for_entries(connection, [row["id"] for row in rows])


def download_response(data, extension, mimetype, when):
    response = send_file(
        io.BytesIO(data), mimetype=mimetype, as_attachment=True,
        download_name=f"my-journal-{when.date().isoformat()}.{extension}",
    )
    # Private data: don't let the browser or a proxy keep a copy.
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/backup")
def backup_page():
    return render_backup_page()


@bp.route("/backup/export.md")
def export_markdown():
    now = datetime.now()
    text = entries_to_markdown(*all_entries_newest_first(), now)
    return download_response(text.encode("utf-8"), "md",
                             "text/markdown", now)


@bp.route("/backup/export.json")
def export_json():
    now = datetime.now()
    text = entries_to_json(*all_entries_newest_first(), now)
    return download_response(text.encode("utf-8"), "json",
                             "application/json", now)


@bp.route("/backup/export.db")
def export_database():
    now = datetime.now()
    # A file-level copy through SQLite's backup API, so it opens its own connections.
    return download_response(database_bytes(current_app.config["DATABASE"]), "db",
                             "application/vnd.sqlite3", now)


@bp.route("/backup/restore", methods=["POST"])
def restore_backup():
    upload = request.files.get("backup_file")
    if upload is None or not upload.filename:
        return render_backup_page("Choose a .json backup file to restore.", 400)
    if not upload.filename.lower().endswith(".json"):
        return render_backup_page(
            "Only .json backups can be restored. Markdown and database "
            "downloads can't be restored here.", 400)
    try:
        entries = parse_backup(upload.read())
    except RestoreError as error:
        return render_backup_page(str(error), 400)

    added, skipped = restore_entries(get_db(), entries)

    flash(restore_message(added, skipped))
    return redirect(url_for("backup.backup_page"))


@bp.app_errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    if request.endpoint == "backup.restore_backup":
        return render_backup_page(
            f"That file is larger than {MAX_UPLOAD_MB} MB, so it can't be restored.",
            413)
    return error
