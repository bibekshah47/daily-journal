""""On this day": entries written a week, a month, months or years ago.

Pure functions over a database connection, so they're easy to test with a
fixed "today".
"""
import calendar
from datetime import date, timedelta

from tags import get_tags_for_entries

# (label, months back). Weeks are handled separately because they're days.
_MONTH_STEPS = [("1 month ago", 1), ("6 months ago", 6), ("1 year ago", 12)]
_COLUMNS = ("id", "title", "content", "mood", "created_at", "is_favorite")


def subtract_months(day, months):
    """Same day N months earlier, clamped: Mar 31 - 1 month = Feb 28 (or 29)."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _about(label):
    """"1 month ago" -> "About a month ago", "6 months ago" -> "About 6 months ago"."""
    if label.startswith("1 "):
        label = "a " + label[2:]
    return "About " + label


def _targets(today, oldest):
    """[(label, target date)] from newest to oldest."""
    targets = [("1 week ago", today - timedelta(weeks=1))]
    targets += [(label, subtract_months(today, months)) for label, months in _MONTH_STEPS]
    years = 2
    # Keep going while the target (or the day after it) could still have entries.
    while subtract_months(today, 12 * years) + timedelta(days=1) >= oldest:
        targets.append((f"{years} years ago", subtract_months(today, 12 * years)))
        years += 1
    return targets


def get_memories(connection, today, limit=3):
    """Up to `limit` memories, oldest first: [{"label": ..., "entry": {...}}].

    For each target date the latest entry on that day is used; failing that,
    the latest entry on the day before, then the day after ("About ...").
    Today's entries and repeats are never included. Each entry carries "tags".
    """
    oldest = connection.execute(
        "SELECT MIN(date(created_at)) FROM entries").fetchone()[0]
    if not oldest or limit <= 0:
        return []
    oldest = date.fromisoformat(oldest)

    targets = _targets(today, oldest)
    one_day = timedelta(days=1)
    days = {day.isoformat() for _, target in targets
            for day in (target - one_day, target, target + one_day)}
    days.discard(today.isoformat())

    # One query for every candidate day; keep the latest entry of each day.
    placeholders = ", ".join("?" for _ in days)
    rows = connection.execute(f"""
        SELECT {", ".join(_COLUMNS)}, date(created_at)
        FROM entries
        WHERE date(created_at) IN ({placeholders})
        ORDER BY created_at, id
    """, sorted(days)).fetchall()
    latest = {}
    for row in rows:
        latest[row[-1]] = dict(zip(_COLUMNS, row[:-1]))

    memories = []
    used = set()
    # Oldest targets first, so the oldest memory wins if two could share an entry.
    for label, target in reversed(targets):
        candidates = [(label, target), (_about(label), target - one_day),
                      (_about(label), target + one_day)]
        for candidate_label, day in candidates:
            entry = latest.get(day.isoformat())
            if day < today and entry and entry["id"] not in used:
                used.add(entry["id"])
                memories.append({"label": candidate_label, "entry": entry})
                break

    memories.sort(key=lambda memory: (memory["entry"]["created_at"], memory["entry"]["id"]))
    memories = memories[:limit]
    tags = get_tags_for_entries(connection, [memory["entry"]["id"] for memory in memories])
    for memory in memories:
        memory["entry"]["tags"] = tags[memory["entry"]["id"]]
    return memories
