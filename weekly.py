"""Weekly review: one Sunday–Saturday week at a glance.

Pure functions over a database connection, so they're easy to test with a
fixed week. Everything reads from live_entries, so trashed entries never appear.
"""
from datetime import date, timedelta

from journal_insights import REFLECTION_QUESTIONS
from tags import get_tags_for_entries

_COLUMNS = ("id", "title", "content", "mood", "created_at", "is_favorite",
            "went_well", "went_wrong", "improve_tomorrow")


def week_bounds(any_date):
    """(sunday, saturday) of the week containing any_date, like the History calendar."""
    sunday = any_date - timedelta(days=(any_date.weekday() + 1) % 7)
    return sunday, sunday + timedelta(days=6)


def week_label(start):
    """"Nov 15 – 21, 2026", "Nov 29 – Dec 5, 2026" or "Dec 27, 2026 – Jan 2, 2027"."""
    end = start + timedelta(days=6)
    first = f"{start:%b} {start.day}"
    if start.year != end.year:
        return f"{first}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{first} – {end:%b} {end.day}, {end.year}"
    return f"{first} – {end.day}, {end.year}"


def get_week_review(connection, start):
    """Everything the /week page shows for the week starting on Sunday `start`."""
    start, end = week_bounds(start)
    rows = connection.execute(f"""
        SELECT {", ".join(_COLUMNS)}, date(created_at)
        FROM live_entries
        WHERE date(created_at) BETWEEN ? AND ?
        ORDER BY created_at, id
    """, (start.isoformat(), end.isoformat())).fetchall()
    entries = [dict(zip(_COLUMNS, row[:-1]), day=date.fromisoformat(row[-1]))
               for row in rows]

    # Rows are oldest first, so the last one seen for a day is its latest entry.
    day_moods = {entry["day"]: entry["mood"] or "" for entry in entries}
    days = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        days.append({"date": day, "written": day in day_moods,
                     "mood": day_moods.get(day, "")})

    reflections = {field: [] for field, _ in REFLECTION_QUESTIONS}
    for entry in entries:
        for field in reflections:
            answer = (entry[field] or "").strip()
            if answer:
                reflections[field].append((entry["day"], answer))

    tags = get_tags_for_entries(connection, [entry["id"] for entry in entries])
    tag_counts = {}
    for entry in entries:
        entry["tags"] = tags[entry["id"]]
        for name in entry["tags"]:
            tag_counts[name] = tag_counts.get(name, 0) + 1

    return {
        "start": start,
        "end": end,
        "days": days,
        "days_written": len(day_moods),
        "entry_count": len(entries),
        "word_count": sum(len((entry["content"] or "").split()) for entry in entries),
        "reflections": reflections,
        "tags": [{"name": name, "count": count} for name, count in
                 sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))],
        "entries": entries[::-1],
    }
