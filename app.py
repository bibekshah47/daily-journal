
# Import Flask tools for creating pages and handling forms
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, abort, send_file)
from werkzeug.exceptions import RequestEntityTooLarge

# Import date and time tools
from datetime import date, datetime, timedelta

# Import SQLite to store journal entries
import sqlite3
import calendar
import os
import secrets
import io
import threading
from database import connect, initialize_database
from backup import (RestoreError, database_bytes, entries_to_json,
                    entries_to_markdown, backup_folder, latest_backup_time,
                    parse_backup, restore_entries, restore_message,
                    run_daily_backup)
from rich_text import prepare_content, safe_journal_html
from journal_insights import REFLECTION_QUESTIONS, get_journal_stats
from memories import get_memories
from tags import (all_tags_with_counts, get_tags_for_entries, normalize_tag,
                  parse_tags, set_entry_tags)
from trash import TRASH_DAYS, cleanup_trash, empty_trash, purge_entry
from weekly import get_week_review, week_bounds, week_label


# Create the Flask application
app = Flask(__name__)

# Flask needs a secret key to show one-time messages ("Entry saved").
# Set SECRET_KEY in your environment to keep messages working across restarts.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)


# Allow isolated databases for testing and explicit upgrades.
app.config["DATABASE"] = os.environ.get("JOURNAL_DATABASE", "journal.db")

# Largest upload accepted (the JSON restore file).
MAX_UPLOAD_MB = 10
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

@app.cli.command("init-db")
def init_db_command():
    """Add missing schema columns without replacing existing data."""
    initialize_database(app.config["DATABASE"])
    print("Database initialized successfully!")


@app.before_request
def ensure_database_schema():
    """Upgrade the configured database before any route reads or writes it."""
    initialize_database(app.config["DATABASE"])


# (database path, day) pairs whose daily backup has already been handled.
_daily_backups_checked = set()
_daily_backup_lock = threading.Lock()


@app.before_request
def ensure_daily_backup():
    """On the first request each day, save a dated copy to backups/,
    then erase entries that have been in Trash for more than 30 days.

    A failed backup is logged and never stops the page from loading. It is
    not retried until the next day (or a server restart), so a persistent
    problem doesn't slow down or spam every request.
    """
    if request.endpoint == "static":
        return
    database_path = app.config["DATABASE"]
    key = (os.path.abspath(database_path), date.today())
    if key in _daily_backups_checked:
        return
    with _daily_backup_lock:
        if key in _daily_backups_checked:
            return
        try:
            run_daily_backup(database_path, key[1])
        except Exception:
            app.logger.exception("Automatic daily backup failed")
        # After the backup, so entries about to be erased are in today's copy.
        try:
            run_trash_cleanup(database_path)
        except Exception:
            app.logger.exception("Automatic trash cleanup failed")
        _daily_backups_checked.add(key)


def run_trash_cleanup(database_path):
    """Erase entries that have been in Trash for more than 30 days."""
    connection = connect(database_path)
    try:
        with connection:
            return cleanup_trash(connection, datetime.now())
    finally:
        connection.close()


app.jinja_env.filters["journal_html"] = safe_journal_html

REFLECTION_FIELDS = ("went_well", "went_wrong", "improve_tomorrow")

# One place to define moods. Templates use this for the mood picker,
# the colored calendar and the mood badges, so they always match.
MOODS = [
    {"name": "Happy", "emoji": "😊", "key": "happy"},
    {"name": "Excited", "emoji": "🤩", "key": "excited"},
    {"name": "Neutral", "emoji": "😐", "key": "neutral"},
    {"name": "Sad", "emoji": "😔", "key": "sad"},
    {"name": "Stressed", "emoji": "😣", "key": "stressed"},
]
MOOD_LOOKUP = {mood["name"]: mood for mood in MOODS}

# A different gentle prompt each day for when the page is blank.
WRITING_PROMPTS = [
    "What's one thing from today you want to remember?",
    "What took up most of your attention today?",
    "Who made your day a little better?",
    "What are you looking forward to this week?",
    "What drained your energy today, and what gave you some back?",
    "What did you learn today, even something small?",
    "If today had a title, what would it be?",
    "What's something you're proud of lately?",
    "What's on your mind right now?",
    "What would make tomorrow a good day?",
]


def parse_timestamp(value):
    """Turn a stored ISO timestamp into a datetime (or None if it's broken)."""
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def friendly_date(value, with_weekday=False):
    moment = parse_timestamp(value) if isinstance(value, str) else value
    if not moment:
        return value or ""
    pattern = "%A, %B %d, %Y" if with_weekday else "%B %d, %Y"
    # Remove the leading zero from the day: "November 03" -> "November 3"
    return moment.strftime(pattern).replace(" 0", " ")


def friendly_time(value):
    moment = parse_timestamp(value) if isinstance(value, str) else value
    if not moment:
        return ""
    return moment.strftime("%I:%M %p").lstrip("0")


def short_date(value):
    """"Nov 3, 2026" for tight spaces such as the backup page stats."""
    moment = parse_timestamp(value) if isinstance(value, str) else value
    if not moment:
        return value or ""
    return moment.strftime("%b %d, %Y").replace(" 0", " ")


def erase_date(deleted_at):
    """The day a trashed entry is due to be erased: "December 16, 2026"."""
    deleted = parse_timestamp(deleted_at)
    return friendly_date(deleted + timedelta(days=TRASH_DAYS)) if deleted else ""


app.jinja_env.filters["friendly_date"] = friendly_date
app.jinja_env.filters["erase_date"] = erase_date
app.jinja_env.filters["short_date"] = short_date
app.jinja_env.filters["friendly_time"] = friendly_time


@app.context_processor
def shared_template_values():
    """Values every template can use."""
    return {"moods": MOODS, "mood_lookup": MOOD_LOOKUP}


def get_connection():
    connection = connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    return connection


def entry_summary(row):
    """Format a database row for the entry lists."""
    created_at = parse_timestamp(row["created_at"])
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "mood": row["mood"],
        "date": friendly_date(created_at) if created_at else row["created_at"],
        "time": friendly_time(created_at),
        "is_favorite": row["is_favorite"],
        "tags": [],
    }


def attach_tags(connection, entries):
    """Fill in each entry summary's "tags" list with one query."""
    tags = get_tags_for_entries(connection, [entry["id"] for entry in entries])
    for entry in entries:
        entry["tags"] = tags.get(entry["id"], [])
    return entries


def tag_suggestions():
    """Existing tag names for the form's <datalist>."""
    connection = connect(app.config["DATABASE"])
    try:
        return [tag["name"] for tag in all_tags_with_counts(connection)]
    finally:
        connection.close()


def safe_next_url(fallback):
    """Only follow redirects that stay inside this app."""
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


# Home Dashboard
@app.route("/")
def home():

    # Get today's date and current time
    today = date.today()
    current_hour = datetime.now().hour

    # Choose a greeting based on the time
    if current_hour < 12:
        greeting = "Good morning"
    elif current_hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    connection = get_connection()
    stats = get_journal_stats(connection, today)

    # The three most recent journal entries
    rows = connection.execute("""
        SELECT *
        FROM live_entries
        ORDER BY created_at DESC, id DESC
        LIMIT 3
    """).fetchall()
    recent_entries = attach_tags(connection, [entry_summary(row) for row in rows])

    # Did the user already write today? Link to that entry if so.
    today_entry = connection.execute("""
        SELECT id, title FROM live_entries
        WHERE date(created_at) = ?
        ORDER BY created_at DESC LIMIT 1
    """, (today.isoformat(),)).fetchone()

    # "On this day": entries from a week, a month, months and years ago
    memories = get_memories(connection, today)

    # Moods for the last 14 days (latest entry of each day)
    start = today - timedelta(days=13)
    mood_rows = connection.execute("""
        SELECT date(created_at) AS day, mood
        FROM live_entries
        WHERE date(created_at) >= ?
        ORDER BY created_at
    """, (start.isoformat(),)).fetchall()
    connection.close()

    day_moods = {}
    for row in mood_rows:
        day_moods[row["day"]] = row["mood"] or ""

    last_two_weeks = []
    for offset in range(14):
        day = start + timedelta(days=offset)
        last_two_weeks.append({
            "date": day,
            "written": day.isoformat() in day_moods,
            "mood": day_moods.get(day.isoformat(), ""),
        })

    prompt = WRITING_PROMPTS[today.toordinal() % len(WRITING_PROMPTS)]

    # Display the Home Dashboard
    return render_template(
        "index.html",
        greeting=greeting,
        today=today,
        total_entries=stats["total_entries"],
        monthly_entries=stats["monthly_entries"],
        current_streak=stats["current_streak"],
        recent_entries=recent_entries,
        today_entry=today_entry,
        memories=memories,
        last_two_weeks=last_two_weeks,
        prompt=prompt,
    )


@app.route("/insights")
def insights():
    connection = connect(app.config["DATABASE"])
    try:
        stats = get_journal_stats(connection)
        mood_counts = dict(connection.execute(
            "SELECT mood, COUNT(*) FROM live_entries WHERE mood IS NOT NULL AND mood != '' GROUP BY mood"
        ).fetchall())
    finally:
        connection.close()

    mood_breakdown = [
        {**mood, "count": mood_counts.get(mood["name"], 0)} for mood in MOODS
    ]
    most_entries = max([mood["count"] for mood in mood_breakdown] + [1])
    return render_template("insights.html", mood_breakdown=mood_breakdown,
                           most_mood_entries=most_entries, **stats)


@app.route("/week")
def week():
    """Weekly review. ?start=YYYY-MM-DD snaps to its Sunday; anything else shows this week."""
    this_week, _ = week_bounds(date.today())
    try:
        start, _ = week_bounds(date.fromisoformat(request.args.get("start", "").strip()))
        if start > this_week:
            raise ValueError("future week")
        previous_start = start - timedelta(days=7)
    except (ValueError, OverflowError):  # OverflowError: weeks before year 1
        start = this_week
        previous_start = start - timedelta(days=7)

    connection = connect(app.config["DATABASE"])
    try:
        review = get_week_review(connection, start)
    finally:
        connection.close()

    # No arrow into the future (and no date overflow near year 9999).
    next_start = start + timedelta(days=7) if start < this_week else None
    return render_template(
        "week.html",
        review=review,
        week_label=week_label(start),
        today=date.today(),
        questions=REFLECTION_QUESTIONS,
        previous_start=previous_start.isoformat(),
        next_start=next_start.isoformat() if next_start else None,
        is_this_week=start == this_week,
    )


# Move an entry to Trash. It keeps its tags and can be restored for 30 days.
@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):

    connection = connect(app.config["DATABASE"])
    try:
        with connection:
            moved = connection.execute(
                "UPDATE entries SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (datetime.now().isoformat(), entry_id),
            ).rowcount
    finally:
        connection.close()

    if moved:
        # The "undo:<id>" category adds an Undo button to the toast.
        flash("Entry moved to Trash.", f"undo:{entry_id}")
    return redirect(url_for("history"))


@app.route("/restore/<int:entry_id>", methods=["POST"])
def restore_entry(entry_id):
    connection = connect(app.config["DATABASE"])
    try:
        with connection:
            restored = connection.execute(
                "UPDATE entries SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
                (entry_id,),
            ).rowcount
    finally:
        connection.close()

    if restored:
        flash("Entry restored.")
    return redirect(safe_next_url(url_for("view_entry", entry_id=entry_id)))


def trash_count(connection):
    return connection.execute(
        "SELECT COUNT(*) FROM entries WHERE deleted_at IS NOT NULL").fetchone()[0]


def erase_in_days(deleted_at, today):
    """Days left before the automatic cleanup erases a trashed entry."""
    deleted = parse_timestamp(deleted_at)
    if not deleted:
        return 0
    return max(0, (deleted.date() + timedelta(days=TRASH_DAYS) - today).days)


@app.route("/trash")
def trash():
    today = date.today()
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT * FROM entries
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC, id DESC
        """).fetchall()
    finally:
        connection.close()

    entries = []
    for row in rows:
        entry = entry_summary(row)
        entry["deleted_at"] = row["deleted_at"]
        entry["created_at"] = row["created_at"]
        entry["erase_in_days"] = erase_in_days(row["deleted_at"], today)
        entries.append(entry)
    return render_template("trash.html", entries=entries, trash_days=TRASH_DAYS)


@app.route("/trash/<int:entry_id>/purge", methods=["POST"])
def purge_trashed_entry(entry_id):
    connection = connect(app.config["DATABASE"])
    try:
        with connection:
            erased = purge_entry(connection, entry_id)
    finally:
        connection.close()

    if erased:
        flash("Entry deleted forever.")
    return redirect(url_for("trash"))


@app.route("/trash/empty", methods=["POST"])
def empty_trash_route():
    connection = connect(app.config["DATABASE"])
    try:
        with connection:
            erased = empty_trash(connection)
    finally:
        connection.close()

    if erased:
        flash("Trash emptied.")
    return redirect(url_for("trash"))


# Edit an existing journal entry
@app.route("/edit/<int:entry_id>", methods=["GET", "POST"])
def edit_entry(entry_id):

    connection = get_connection()
    cursor = connection.cursor()

    # Entries in Trash can't be edited; their page offers Restore instead.
    status = cursor.execute(
        "SELECT deleted_at FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if status is not None and status["deleted_at"]:
        connection.close()
        return redirect(url_for("view_entry", entry_id=entry_id))

    # Check whether the user submitted the edit form
    if request.method == "POST":

        # Get the updated journal text and mood
        updated_content, content_html = prepare_content(request.form)
        updated_mood = request.form.get("mood")
        updated_title = request.form.get("title", "").strip()
        updated_tags = parse_tags(request.form.get("tags", ""))
        reflections = {field: request.form.get(field, "").strip()
                       for field in REFLECTION_FIELDS}

        # Make sure the updated journal text is not empty
        if updated_content and updated_content.strip():

            cursor.execute(
                """
                UPDATE entries
                SET content = ?, mood = ?, title = ?, content_html = ?,
                    went_well = ?, went_wrong = ?, improve_tomorrow = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (updated_content, updated_mood, updated_title, content_html,
                 *(reflections[field] for field in REFLECTION_FIELDS), entry_id)
            )
            if cursor.rowcount:
                set_entry_tags(connection, entry_id, updated_tags)

            connection.commit()
            connection.close()

            flash("Changes saved.")
            return redirect(url_for("view_entry", entry_id=entry_id))

    # Retrieve the journal entry being edited
    cursor.execute("SELECT * FROM live_entries WHERE id = ?", (entry_id,))
    row = cursor.fetchone()
    entry = dict(row) if row is not None else None
    if entry is not None:
        entry["tags"] = get_tags_for_entries(connection, [entry_id])[entry_id]
    connection.close()

    if entry is None:
        abort(404)

    # Keep every submitted field when validation fails.
    if request.method == "POST":
        entry = dict(entry)
        entry.update(title=updated_title, content=updated_content or "",
                     mood=updated_mood, content_html=content_html,
                     tags=updated_tags, **reflections)
        return render_template("edit.html", entry=entry,
                               tag_suggestions=tag_suggestions(),
                               error="Write something in your entry before saving."), 400

    return render_template("edit.html", entry=entry,
                           tag_suggestions=tag_suggestions())


# Favorite or unfavorite a journal entry
@app.route("/favorite/<int:entry_id>", methods=["POST"])
def toggle_favorite(entry_id):

    connection = connect(app.config["DATABASE"])
    connection.execute("""
        UPDATE entries
        SET is_favorite = CASE WHEN is_favorite = 1 THEN 0 ELSE 1 END
        WHERE id = ? AND deleted_at IS NULL
    """, (entry_id,))
    connection.commit()
    connection.close()

    # Preserve the active search, date and favorites filters
    params = {
        "search": request.form.get("search", ""),
        "filter_date": request.form.get("filter_date", ""),
        "month": request.form.get("month", ""),
        "favorites": request.form.get("favorites", ""),
        "tag": request.form.get("tag", ""),
    }
    params = {key: value for key, value in params.items() if value}

    fallback = url_for("history", **params) + f"#entry-{entry_id}"
    return redirect(safe_next_url(fallback))


@app.route("/history")
def history():
    today = date.today()

    # Read filters from the URL
    search_query = request.args.get("search", "").strip()
    filter_date = request.args.get("filter_date", "").strip()
    selected_month = request.args.get("month", "").strip()
    favorites_only = request.args.get("favorites", "") == "1"
    active_tag = normalize_tag(request.args.get("tag", ""))

    # Validate the selected date
    if filter_date:
        try:
            date.fromisoformat(filter_date)
        except ValueError:
            filter_date = ""

    # Decide which month the calendar should display
    try:
        if selected_month:
            displayed_month = datetime.strptime(selected_month, "%Y-%m").date()
        elif filter_date:
            displayed_month = date.fromisoformat(filter_date).replace(day=1)
        else:
            displayed_month = today.replace(day=1)
    except ValueError:
        displayed_month = today.replace(day=1)

    current_year = displayed_month.year
    current_month = displayed_month.month

    # Generate the calendar
    cal = calendar.Calendar(firstweekday=6)
    calendar_weeks = cal.monthdatescalendar(current_year, current_month)

    previous_month = (displayed_month - timedelta(days=1)).strftime("%Y-%m")
    if current_month == 12:
        next_month = f"{current_year + 1}-01"
    else:
        next_month = f"{current_year}-{current_month + 1:02d}"

    connection = get_connection()
    cursor = connection.cursor()

    # Dates that contain journal entries, with the mood of the latest one
    cursor.execute("""
        SELECT date(created_at) AS entry_date, mood
        FROM live_entries
        ORDER BY created_at
    """)
    calendar_moods = {}
    for row in cursor.fetchall():
        calendar_moods[row["entry_date"]] = row["mood"] or ""
    calendar_entry_dates = set(calendar_moods)

    # Build the journal search query
    query = "SELECT * FROM live_entries WHERE 1=1"
    parameters = []

    if favorites_only:
        query += " AND is_favorite = 1"

    if search_query:
        query += """ AND (title LIKE ? OR content LIKE ? OR id IN (
            SELECT entry_tags.entry_id FROM entry_tags
            JOIN tags ON tags.id = entry_tags.tag_id
            WHERE tags.name LIKE ?))"""
        keyword = f"%{search_query}%"
        # "#work" should find the tag "work".
        tag_keyword = f"%{search_query.lstrip('#')}%"
        parameters.extend([keyword, keyword, tag_keyword])

    if active_tag:
        query += """ AND id IN (
            SELECT entry_tags.entry_id FROM entry_tags
            JOIN tags ON tags.id = entry_tags.tag_id
            WHERE tags.name = ?)"""
        parameters.append(active_tag)

    if filter_date:
        query += " AND date(created_at) = ?"
        parameters.append(filter_date)

    query += " ORDER BY created_at DESC, id DESC"

    cursor.execute(query, parameters)
    rows = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM live_entries")
    total_entries = cursor.fetchone()[0]
    trashed_entries = trash_count(connection)
    entries = attach_tags(connection, [entry_summary(row) for row in rows])
    connection.close()

    # Group entries by month so the list is easier to scan
    groups = []
    for row, entry in zip(rows, entries):
        created_at = parse_timestamp(row["created_at"])
        label = created_at.strftime("%B %Y") if created_at else "Other"
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "entries": []})
        groups[-1]["entries"].append(entry)

    return render_template(
        "history.html",
        entries=entries,
        entry_groups=groups,
        total_entries=total_entries,
        search_query=search_query,
        filter_date=filter_date,
        displayed_month=displayed_month,
        current_month=current_month,
        calendar_weeks=calendar_weeks,
        previous_month=previous_month,
        next_month=next_month,
        calendar_entry_dates=calendar_entry_dates,
        calendar_moods=calendar_moods,
        favorites_only=favorites_only,
        active_tag=active_tag,
        trashed_entries=trashed_entries,
        today=today,
    )


# View a single journal entry
@app.route("/entry/<int:entry_id>")
def view_entry(entry_id):

    connection = get_connection()
    # "entries", not "live_entries": a trashed entry opens with a Restore notice.
    row = connection.execute(
        "SELECT * FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()

    if row is None:
        connection.close()
        abort(404)

    entry = dict(row)
    entry["tags"] = get_tags_for_entries(connection, [entry_id])[entry_id]

    # Find the next older and newer entries for "previous / next" links
    older = connection.execute("""
        SELECT id, title, created_at FROM live_entries
        WHERE created_at < ? OR (created_at = ? AND id < ?)
        ORDER BY created_at DESC, id DESC LIMIT 1
    """, (entry["created_at"], entry["created_at"], entry_id)).fetchone()
    newer = connection.execute("""
        SELECT id, title, created_at FROM live_entries
        WHERE created_at > ? OR (created_at = ? AND id > ?)
        ORDER BY created_at ASC, id ASC LIMIT 1
    """, (entry["created_at"], entry["created_at"], entry_id)).fetchone()
    connection.close()

    return render_template("entry.html", entry=entry, older=older, newer=newer)


# Dedicated Write Journal page
@app.route("/write", methods=["GET", "POST"])
def write_journal():

    # When the user submits the journal form
    if request.method == "POST":

        title = request.form.get("title", "").strip()
        mood = request.form.get("mood", "").strip()
        tags = parse_tags(request.form.get("tags", ""))
        content, content_html = prepare_content(request.form)
        reflections = {field: request.form.get(field, "").strip()
                       for field in REFLECTION_FIELDS}

        # Prevent empty journal entries
        if not content:
            return render_template(
                "write.html",
                error="Write something in your entry before saving.",
                title=title,
                mood=mood,
                tags=tags,
                tag_suggestions=tag_suggestions(),
                content=content,
                content_html=content_html,
                prompt=WRITING_PROMPTS[date.today().toordinal() % len(WRITING_PROMPTS)],
                **reflections
            ), 400

        created_at = datetime.now().isoformat()

        connection = connect(app.config["DATABASE"])
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO entries
                (title, content, mood, created_at, content_html,
                 went_well, went_wrong, improve_tomorrow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, content, mood, created_at, content_html,
              *(reflections[field] for field in REFLECTION_FIELDS)))
        entry_id = cursor.lastrowid
        set_entry_tags(connection, entry_id, tags)
        connection.commit()
        connection.close()

        flash("Entry saved.")
        return redirect(url_for("view_entry", entry_id=entry_id, saved="1"))

    # Display an empty writing form
    return render_template(
        "write.html",
        error=None,
        title="",
        mood="",
        tags=[],
        tag_suggestions=tag_suggestions(),
        content="",
        prompt=WRITING_PROMPTS[date.today().toordinal() % len(WRITING_PROMPTS)],
        **{field: "" for field in REFLECTION_FIELDS}
    )


# Backup & export
def render_backup_page(error=None, status=200):
    connection = connect(app.config["DATABASE"])
    try:
        total, oldest, newest = connection.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM live_entries"
        ).fetchone()
    finally:
        connection.close()
    try:
        last_backup = latest_backup_time(app.config["DATABASE"])
    except OSError:
        last_backup = None
    return render_template(
        "backup.html",
        error=error,
        total_entries=total,
        oldest_entry=oldest,
        newest_entry=newest,
        last_backup=last_backup,
        backup_folder=backup_folder(app.config["DATABASE"]),
        max_upload_mb=MAX_UPLOAD_MB,
    ), status


def all_entries_newest_first():
    """Every entry not in Trash, newest first, plus {entry_id: [tag names]}."""
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM live_entries ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return rows, get_tags_for_entries(connection, [row["id"] for row in rows])
    finally:
        connection.close()


def download_response(data, extension, mimetype, when):
    response = send_file(
        io.BytesIO(data), mimetype=mimetype, as_attachment=True,
        download_name=f"my-journal-{when.date().isoformat()}.{extension}",
    )
    # Private data: don't let the browser or a proxy keep a copy.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/backup")
def backup_page():
    return render_backup_page()


@app.route("/backup/export.md")
def export_markdown():
    now = datetime.now()
    text = entries_to_markdown(*all_entries_newest_first(), now)
    return download_response(text.encode("utf-8"), "md",
                             "text/markdown", now)


@app.route("/backup/export.json")
def export_json():
    now = datetime.now()
    text = entries_to_json(*all_entries_newest_first(), now)
    return download_response(text.encode("utf-8"), "json",
                             "application/json", now)


@app.route("/backup/export.db")
def export_database():
    now = datetime.now()
    return download_response(database_bytes(app.config["DATABASE"]), "db",
                             "application/vnd.sqlite3", now)


@app.route("/backup/restore", methods=["POST"])
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

    connection = connect(app.config["DATABASE"])
    try:
        added, skipped = restore_entries(connection, entries)
    finally:
        connection.close()

    flash(restore_message(added, skipped))
    return redirect(url_for("backup_page"))


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    if request.endpoint == "restore_backup":
        return render_backup_page(
            f"That file is larger than {MAX_UPLOAD_MB} MB, so it can't be restored.",
            413)
    return error


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=True, port=5001)
