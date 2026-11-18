"""Home, writing, reading and editing entries, favorites, and Trash."""
from datetime import timedelta

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user

from accounts import first_name

from db import get_db
from journal_insights import get_journal_stats
from memories import get_memories
from rich_text import prepare_content
from tags import get_tags_for_entries, parse_tags, set_entry_tags
from templates_data import WRITING_TEMPLATES
from timeutil import user_now, user_today
from trash import TRASH_DAYS, empty_trash, purge_entry
from views.shared import (REFLECTION_FIELDS, attach_tags, entry_summary,
                          owned_entry_or_404, parse_timestamp, safe_next_url,
                          tag_suggestions, user_id)

bp = Blueprint("entries", __name__)

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


def todays_prompt(today):
    """The prompt for `today` (the user's day): the same one all day."""
    return WRITING_PROMPTS[today.toordinal() % len(WRITING_PROMPTS)]


# Home Dashboard
@bp.route("/")
def home():

    # The user's own date and time, wherever the server is.
    now = user_now()
    today = now.date()
    current_hour = now.hour

    # Choose a greeting based on the time
    if current_hour < 12:
        greeting = "Good morning"
    elif current_hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    connection = get_db()
    me = user_id()
    stats = get_journal_stats(connection, today, user_id=me)

    # The three most recent journal entries
    rows = connection.execute("""
        SELECT *
        FROM live_entries
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 3
    """, (me,)).fetchall()
    recent_entries = attach_tags(connection, [entry_summary(row) for row in rows])

    # Did the user already write today? Link to that entry if so.
    today_entry = connection.execute("""
        SELECT id, title FROM live_entries
        WHERE user_id = ? AND substr(created_at, 1, 10) = ?
        ORDER BY created_at DESC LIMIT 1
    """, (me, today.isoformat())).fetchone()

    # "On this day": entries from a week, a month, months and years ago
    memories = get_memories(connection, today, user_id=me)

    # Moods for the last 14 days (latest entry of each day)
    start = today - timedelta(days=13)
    mood_rows = connection.execute("""
        SELECT substr(created_at, 1, 10) AS day, mood
        FROM live_entries
        WHERE user_id = ? AND substr(created_at, 1, 10) >= ?
        ORDER BY created_at, id
    """, (me, start.isoformat())).fetchall()

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

    prompt = todays_prompt(today)

    # Display the Home Dashboard
    return render_template(
        "index.html",
        greeting=greeting,
        greeting_name=first_name(current_user.display_name),
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


# Dedicated Write Journal page
@bp.route("/write", methods=["GET", "POST"])
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
                prompt=todays_prompt(user_today()),
                writing_templates=WRITING_TEMPLATES,
                **reflections
            ), 400

        # The writer's wall-clock time, like every existing entry.
        created_at = user_now().isoformat()

        connection = get_db()
        entry_id = connection.insert("""
            INSERT INTO entries
                (user_id, title, content, mood, created_at, content_html,
                 went_well, went_wrong, improve_tomorrow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id(), title, content, mood, created_at, content_html,
              *(reflections[field] for field in REFLECTION_FIELDS)))
        set_entry_tags(connection, entry_id, tags, user_id=user_id())
        connection.commit()

        flash("Entry saved.")
        return redirect(url_for("entries.view_entry", entry_id=entry_id, saved="1"))

    # Display an empty writing form
    return render_template(
        "write.html",
        error=None,
        title="",
        mood="",
        tags=[],
        tag_suggestions=tag_suggestions(),
        content="",
        prompt=todays_prompt(user_today()),
        writing_templates=WRITING_TEMPLATES,
        **{field: "" for field in REFLECTION_FIELDS}
    )


# View a single journal entry
@bp.route("/entry/<int:entry_id>")
def view_entry(entry_id):

    connection = get_db()
    me = user_id()
    # Any of the user's entries, in Trash or not: a trashed one opens with a Restore notice.
    entry = dict(owned_entry_or_404(entry_id))
    entry["tags"] = get_tags_for_entries(connection, [entry_id], user_id=me)[entry_id]

    # Find the next older and newer entries for "previous / next" links
    older = connection.execute("""
        SELECT id, title, created_at FROM live_entries
        WHERE user_id = ? AND (created_at < ? OR (created_at = ? AND id < ?))
        ORDER BY created_at DESC, id DESC LIMIT 1
    """, (me, entry["created_at"], entry["created_at"], entry_id)).fetchone()
    newer = connection.execute("""
        SELECT id, title, created_at FROM live_entries
        WHERE user_id = ? AND (created_at > ? OR (created_at = ? AND id > ?))
        ORDER BY created_at ASC, id ASC LIMIT 1
    """, (me, entry["created_at"], entry["created_at"], entry_id)).fetchone()

    return render_template("entry.html", entry=entry, older=older, newer=newer)


# Edit an existing journal entry
@bp.route("/edit/<int:entry_id>", methods=["GET", "POST"])
def edit_entry(entry_id):

    connection = get_db()
    me = user_id()

    # Someone else's entry is a 404. Entries in Trash can't be edited; their
    # page offers Restore instead.
    if owned_entry_or_404(entry_id, "deleted_at")["deleted_at"]:
        return redirect(url_for("entries.view_entry", entry_id=entry_id))

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

            cursor = connection.execute(
                """
                UPDATE entries
                SET content = ?, mood = ?, title = ?, content_html = ?,
                    went_well = ?, went_wrong = ?, improve_tomorrow = ?
                WHERE id = ? AND user_id = ? AND deleted_at IS NULL
                """,
                (updated_content, updated_mood, updated_title, content_html,
                 *(reflections[field] for field in REFLECTION_FIELDS), entry_id, me)
            )
            if cursor.rowcount:
                set_entry_tags(connection, entry_id, updated_tags, user_id=me)

            connection.commit()

            flash("Changes saved.")
            return redirect(url_for("entries.view_entry", entry_id=entry_id))

    # Retrieve the journal entry being edited
    row = connection.execute(
        "SELECT * FROM live_entries WHERE id = ? AND user_id = ?", (entry_id, me)).fetchone()
    if row is None:
        abort(404)
    entry = dict(row)
    entry["tags"] = get_tags_for_entries(connection, [entry_id], user_id=me)[entry_id]

    # Keep every submitted field when validation fails.
    if request.method == "POST":
        entry.update(title=updated_title, content=updated_content or "",
                     mood=updated_mood, content_html=content_html,
                     tags=updated_tags, **reflections)
        return render_template("edit.html", entry=entry,
                               tag_suggestions=tag_suggestions(),
                               error="Write something in your entry before saving."), 400

    return render_template("edit.html", entry=entry,
                           tag_suggestions=tag_suggestions())


# Favorite or unfavorite a journal entry
@bp.route("/favorite/<int:entry_id>", methods=["POST"])
def toggle_favorite(entry_id):

    owned_entry_or_404(entry_id, "id")
    connection = get_db()
    connection.execute("""
        UPDATE entries
        SET is_favorite = CASE WHEN is_favorite = 1 THEN 0 ELSE 1 END
        WHERE id = ? AND user_id = ? AND deleted_at IS NULL
    """, (entry_id, user_id()))
    connection.commit()

    # Preserve the active search, date and favorites filters
    params = {
        "search": request.form.get("search", ""),
        "filter_date": request.form.get("filter_date", ""),
        "month": request.form.get("month", ""),
        "favorites": request.form.get("favorites", ""),
        "tag": request.form.get("tag", ""),
    }
    params = {key: value for key, value in params.items() if value}

    fallback = url_for("history.history", **params) + f"#entry-{entry_id}"
    return redirect(safe_next_url(fallback))


# Move an entry to Trash. It keeps its tags and can be restored for 30 days.
@bp.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):

    owned_entry_or_404(entry_id, "id")
    connection = get_db()
    with connection:
        moved = connection.execute(
            "UPDATE entries SET deleted_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (user_now().isoformat(), entry_id, user_id()),
        ).rowcount

    if moved:
        # The "undo:<id>" category adds an Undo button to the toast.
        flash("Entry moved to Trash.", f"undo:{entry_id}")
    return redirect(url_for("history.history"))


@bp.route("/restore/<int:entry_id>", methods=["POST"])
def restore_entry(entry_id):
    owned_entry_or_404(entry_id, "id")
    connection = get_db()
    with connection:
        restored = connection.execute(
            "UPDATE entries SET deleted_at = NULL "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (entry_id, user_id()),
        ).rowcount

    if restored:
        flash("Entry restored.")
    return redirect(safe_next_url(url_for("entries.view_entry", entry_id=entry_id)))


def erase_in_days(deleted_at, today):
    """Days left before the automatic cleanup erases a trashed entry."""
    deleted = parse_timestamp(deleted_at)
    if not deleted:
        return 0
    return max(0, (deleted.date() + timedelta(days=TRASH_DAYS) - today).days)


@bp.route("/trash")
def trash():
    today = user_today()
    rows = get_db().execute("""
        SELECT * FROM entries
        WHERE user_id = ? AND deleted_at IS NOT NULL
        ORDER BY deleted_at DESC, id DESC
    """, (user_id(),)).fetchall()

    entries = []
    for row in rows:
        entry = entry_summary(row)
        entry["deleted_at"] = row["deleted_at"]
        entry["created_at"] = row["created_at"]
        entry["erase_in_days"] = erase_in_days(row["deleted_at"], today)
        entries.append(entry)
    return render_template("trash.html", entries=entries, trash_days=TRASH_DAYS)


@bp.route("/trash/<int:entry_id>/purge", methods=["POST"])
def purge_trashed_entry(entry_id):
    owned_entry_or_404(entry_id, "id")
    connection = get_db()
    with connection:
        erased = purge_entry(connection, entry_id, user_id=user_id())

    if erased:
        flash("Entry deleted forever.")
    return redirect(url_for("entries.trash"))


@bp.route("/trash/empty", methods=["POST"])
def empty_trash_route():
    connection = get_db()
    with connection:
        erased = empty_trash(connection, user_id=user_id())

    if erased:
        flash("Trash emptied.")
    return redirect(url_for("entries.trash"))
