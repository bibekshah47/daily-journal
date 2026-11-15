"""Export, restore and automatic daily backups for the journal database."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from journal_insights import REFLECTION_QUESTIONS
from rich_text import clean_html

EXPORT_APP = "my-journal"
EXPORT_VERSION = 1
KEEP_DAILY_BACKUPS = 14
BACKUP_NAME = re.compile(r"^journal-(\d{4}-\d{2}-\d{2})\.db$")

# Columns a restore may write. "id" is left out on purpose: restored entries get
# new ids so they can never replace an existing entry.
TEXT_COLUMNS = ("title", "content", "mood", "created_at", "content_html",
                "went_well", "went_wrong", "improve_tomorrow")


class RestoreError(ValueError):
    """The uploaded file is not a backup this app can restore."""


# ---------- SQLite copies ----------

def _open_read_only(database_path):
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def copy_database(database_path, destination):
    """Copy the database with SQLite's online backup API (consistent even mid-write)."""
    source = _open_read_only(database_path)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def database_bytes(database_path):
    """Return an exact backup of the database as bytes, for downloading."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "export.db")
        copy_database(database_path, path)
        return Path(path).read_bytes()


# ---------- Automatic daily backups ----------

def backup_folder(database_path):
    return Path(database_path).resolve().parent / "backups"


def list_backups(database_path):
    """Daily backup files, oldest first. Other files in the folder are ignored."""
    folder = backup_folder(database_path)
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if path.is_file() and BACKUP_NAME.match(path.name))


def latest_backup_time(database_path):
    backups = list_backups(database_path)
    if not backups:
        return None
    return datetime.fromtimestamp(backups[-1].stat().st_mtime)


def run_daily_backup(database_path, today=None, keep=KEEP_DAILY_BACKUPS):
    """Create today's backup if it is missing, then prune old ones.

    Returns the path of the backup that was created, or None if today's
    backup already existed (or the database doesn't exist yet).
    """
    today = today or date.today()
    if not Path(database_path).exists():
        return None
    folder = backup_folder(database_path)
    target = folder / f"journal-{today.isoformat()}.db"
    created = None
    if not target.exists():
        folder.mkdir(exist_ok=True)
        # Write to a temporary name first so a half-written file is never
        # mistaken for a finished backup.
        partial = folder / f".{target.name}.partial"
        try:
            copy_database(database_path, partial)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        created = target
    for old in list_backups(database_path)[:-keep]:
        old.unlink()
    return created


# ---------- Exports ----------

def _friendly(moment, pattern):
    return moment.strftime(pattern).replace(" 0", " ")


def _entry_moment(created_at):
    try:
        moment = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return created_at or "", created_at or ""
    when = (_friendly(moment, "%A, %B %d, %Y") + " at "
            + moment.strftime("%I:%M %p").lstrip("0"))
    return _friendly(moment, "%B %d, %Y"), when


def entries_to_markdown(rows, exported_at):
    """One Markdown document, newest entry first, entries separated by ---."""
    count = len(rows)
    parts = [
        "# My Journal\n\n"
        f"Exported {_friendly(exported_at, '%B %d, %Y')} · "
        f"{count} {'entry' if count == 1 else 'entries'}\n"
    ]
    for row in rows:
        day, when = _entry_moment(row["created_at"])
        title = (row["title"] or "").strip() or day
        lines = [f"## {title}", "", when]
        if (row["mood"] or "").strip():
            lines.append(f"Mood: {row['mood'].strip()}")
        lines += ["", (row["content"] or "").strip()]
        answers = [(question, (row[field] or "").strip())
                   for field, question in REFLECTION_QUESTIONS]
        answers = [(question, answer) for question, answer in answers if answer]
        if answers:
            lines += ["", "### Daily reflection"]
            for question, answer in answers:
                lines += ["", f"**{question}**", answer]
        parts.append("\n".join(lines) + "\n")
    return "\n---\n\n".join(parts)


def entries_to_json(rows, exported_at):
    payload = {
        "app": EXPORT_APP,
        "version": EXPORT_VERSION,
        "exported_at": exported_at.isoformat(timespec="seconds"),
        "entries": [dict(row) for row in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ---------- Restore ----------

def _text(value, label, number):
    if value is None or isinstance(value, str):
        return value
    raise RestoreError(f"Entry {number} has an invalid {label}.")


def parse_backup(raw_bytes):
    """Validate an uploaded JSON export and return its entries ready to insert.

    Raises RestoreError with a message meant for the person using the app.
    Nothing is checked lazily: a bad entry anywhere rejects the whole file.
    """
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RestoreError("That file isn't valid JSON. Choose a .json file "
                           "downloaded from this page.") from None
    if (not isinstance(payload, dict) or payload.get("app") != EXPORT_APP
            or "entries" not in payload):
        raise RestoreError("That JSON file isn't a My Journal backup. Choose a "
                           "file made with “Download .json” on this page.")
    if payload.get("version") != EXPORT_VERSION:
        raise RestoreError(f"This backup uses format version "
                           f"{payload.get('version')!r}, but this app can only "
                           f"restore version {EXPORT_VERSION}.")
    if not isinstance(payload["entries"], list):
        raise RestoreError("The backup's list of entries is damaged.")

    entries = []
    for number, item in enumerate(payload["entries"], start=1):
        if not isinstance(item, dict):
            raise RestoreError(f"Entry {number} in the backup is damaged.")
        entry = {column: _text(item.get(column), column.replace("_", " "), number)
                 for column in TEXT_COLUMNS}
        if not (entry["content"] or "").strip() or not (entry["created_at"] or "").strip():
            raise RestoreError(f"Entry {number} is missing its text or date.")
        favorite = item.get("is_favorite", 0)
        if favorite in (None, ""):
            favorite = 0
        if not isinstance(favorite, (bool, int)):
            raise RestoreError(f"Entry {number} has an invalid favorite value.")
        entry["is_favorite"] = 1 if favorite else 0
        if entry["content_html"] is not None:
            entry["content_html"] = clean_html(entry["content_html"])
        entries.append(entry)
    return entries


def restore_entries(connection, entries):
    """Insert entries that aren't already present. Returns (added, skipped).

    An entry is a duplicate when both created_at and content match an existing
    entry (or an earlier entry in the same file). Nothing is updated or deleted.
    """
    columns = TEXT_COLUMNS + ("is_favorite",)
    placeholders = ", ".join("?" for _ in columns)
    added = skipped = 0
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        seen = {(row[0], row[1]) for row in
                connection.execute("SELECT created_at, content FROM entries")}
        # Oldest first, so restored entries get ids in the order they were written.
        for entry in sorted(entries, key=lambda item: item["created_at"]):
            key = (entry["created_at"], entry["content"])
            if key in seen:
                skipped += 1
                continue
            connection.execute(
                f"INSERT INTO entries ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(entry[column] for column in columns),
            )
            seen.add(key)
            added += 1
    return added, skipped


def restore_message(added, skipped):
    def entries(n):
        return f"{n} {'entry' if n == 1 else 'entries'}"
    if not added and not skipped:
        return "That backup has no entries, so nothing was restored."
    if not added:
        return (f"Nothing new to restore. All {entries(skipped)} were already here."
                if skipped > 1 else "Nothing new to restore. That entry was already here.")
    message = f"Restored {entries(added)}."
    if skipped:
        message += (f" Skipped {skipped} that {'was' if skipped == 1 else 'were'}"
                    " already here.")
    return message
