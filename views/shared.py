"""Values, template filters and small helpers used by several blueprints."""
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import abort, request, session
from flask_login import current_user

from config import APP_NAME, APP_TAGLINE
from db import get_db
from rich_text import safe_journal_html
from tags import all_tags_with_counts, get_tags_for_entries
from trash import TRASH_DAYS

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


# ---------- Dates ----------

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


def register_template_helpers(app):
    """Filters and values every template can use."""
    app.jinja_env.filters["journal_html"] = safe_journal_html
    app.jinja_env.filters["friendly_date"] = friendly_date
    app.jinja_env.filters["erase_date"] = erase_date
    app.jinja_env.filters["short_date"] = short_date
    app.jinja_env.filters["friendly_time"] = friendly_time

    @app.context_processor
    def shared_template_values():
        return {"APP_NAME": APP_NAME, "APP_TAGLINE": APP_TAGLINE,
                "moods": MOODS, "mood_lookup": MOOD_LOOKUP, "draft_key": draft_key(),
                # Set when an account is deleted: the next page clears its draft.
                "clear_draft_key": pop_clear_draft_key()}


def pop_clear_draft_key():
    """The deleted account's draft key, once. (Checked first: popping a
    missing key would still mark the session changed and resend the cookie.)"""
    return session.pop("clear_draft_key") if "clear_draft_key" in session else None


# ---------- The signed-in user ----------

def user_id():
    """The signed-in user's id. Every query on journal data filters by it."""
    return current_user.id


def draft_key():
    """The Write page's localStorage key: one per user, so people sharing a
    computer never see each other's unsaved drafts."""
    if not current_user.is_authenticated:
        return None
    return f"my-journal-new-entry-draft-{current_user.id}"


def owned_entry_or_404(entry_id, columns="*"):
    """One of the user's entries (in Trash or not), or a 404.

    Someone else's entry gets the same 404 as a missing one, so ids can't be
    probed. `columns` is always a fixed string from the calling code.
    """
    row = get_db().execute(
        f"SELECT {columns} FROM entries WHERE id = ? AND user_id = ?",
        (entry_id, user_id())).fetchone()
    if row is None:
        abort(404)
    return row


# ---------- Entries ----------

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
    tags = get_tags_for_entries(connection, [entry["id"] for entry in entries],
                                user_id=user_id())
    for entry in entries:
        entry["tags"] = tags.get(entry["id"], [])
    return entries


def tag_suggestions():
    """Existing tag names for the form's <datalist>."""
    return [tag["name"] for tag in all_tags_with_counts(get_db(), user_id=user_id())]


def trash_count(connection):
    return connection.execute(
        "SELECT COUNT(*) FROM entries WHERE deleted_at IS NOT NULL AND user_id = ?",
        (user_id(),)).fetchone()[0]


def safe_path(target):
    """`target` if it is a path inside this app ("/history?tag=x"), else None.

    Rejects other sites in every form browsers accept: "//evil.com",
    "https://evil.com", "/\\evil.com" (browsers read "\\" as "/") and paths
    with spaces or control characters (browsers drop some of them).
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


def safe_next_url(fallback):
    """The form's "next" path if it stays inside this app, else `fallback`."""
    return safe_path(request.form.get("next", "")) or fallback
