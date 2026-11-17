"""Values, template filters and small helpers used by several blueprints."""
from datetime import datetime, timedelta

from flask import request

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
        return {"moods": MOODS, "mood_lookup": MOOD_LOOKUP}


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
    tags = get_tags_for_entries(connection, [entry["id"] for entry in entries])
    for entry in entries:
        entry["tags"] = tags.get(entry["id"], [])
    return entries


def tag_suggestions():
    """Existing tag names for the form's <datalist>."""
    return [tag["name"] for tag in all_tags_with_counts(get_db())]


def trash_count(connection):
    return connection.execute(
        "SELECT COUNT(*) FROM entries WHERE deleted_at IS NOT NULL").fetchone()[0]


def safe_next_url(fallback):
    """Only follow redirects that stay inside this app."""
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return fallback
