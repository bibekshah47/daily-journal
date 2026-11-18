"""Backup & export: downloads, and restoring a JSON backup."""
from datetime import datetime
import io

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from werkzeug.exceptions import RequestEntityTooLarge

from backup import (RestoreError, entries_to_json, entries_to_markdown,
                    latest_backup_time, parse_backup, restore_entries,
                    restore_message)
from config import MAX_UPLOAD_MB
from db import get_db, uses_postgres
from tags import get_tags_for_entries
from views.shared import user_id

bp = Blueprint("backup", __name__)


def render_backup_page(error=None, status=200):
    total, oldest, newest = get_db().execute(
        "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM live_entries WHERE user_id = ?",
        (user_id(),)).fetchone()
    # The daily backup copies the SQLite file on the server (never shown: only
    # its time). PostgreSQL has no such copies.
    file_backups = not uses_postgres()
    last_backup = None
    if file_backups:
        try:
            last_backup = latest_backup_time(current_app.config["DATABASE"])
        except OSError:
            last_backup = None
    return render_template(
        "backup.html",
        error=error,
        total_entries=total,
        oldest_entry=oldest,
        newest_entry=newest,
        last_backup=last_backup,
        file_backups=file_backups,
        max_upload_mb=MAX_UPLOAD_MB,
    ), status


def all_entries_newest_first():
    """The user's entries not in Trash, newest first, plus {entry_id: [tag names]}."""
    connection = get_db()
    me = user_id()
    rows = connection.execute(
        "SELECT * FROM live_entries WHERE user_id = ? ORDER BY created_at DESC, id DESC",
        (me,)).fetchall()
    return rows, get_tags_for_entries(connection, [row["id"] for row in rows], user_id=me)


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

    added, skipped = restore_entries(get_db(), entries, user_id=user_id())

    flash(restore_message(added, skipped))
    return redirect(url_for("backup.backup_page"))


@bp.app_errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    if request.endpoint == "backup.restore_backup":
        return render_backup_page(
            f"That file is larger than {MAX_UPLOAD_MB} MB, so it can't be restored.",
            413)
    return error
