"""Tag rules shared by the forms, History filter and backup restore.

A tag is lowercase letters, numbers, "-" and "_" only, at most MAX_TAG_LENGTH
characters. An entry has at most MAX_TAGS_PER_ENTRY tags and no duplicates.
None of these functions commit: callers own the transaction.
"""
import re

MAX_TAG_LENGTH = 30
MAX_TAGS_PER_ENTRY = 5

_SPACES = re.compile(r"\s+")


def normalize_tag(text):
    """"  #Deep Work " -> "deep-work". Returns "" for nothing usable."""
    text = str(text or "").strip().lstrip("#").strip().lower()
    text = _SPACES.sub("-", text)
    text = "".join(ch for ch in text if ch.isalnum() or ch in "-_")
    return text[:MAX_TAG_LENGTH]


def normalize_tags(values):
    """Normalize a list of tags: drop empties and duplicates, keep the first 5."""
    tags = []
    for value in values:
        tag = normalize_tag(value)
        if tag and tag not in tags:
            tags.append(tag)
            if len(tags) == MAX_TAGS_PER_ENTRY:
                break
    return tags


def parse_tags(text):
    """Parse the form's comma-separated list: "Work, #deep work" -> ["work", "deep-work"]."""
    return normalize_tags(str(text or "").split(","))


def delete_unused_tags(connection):
    connection.execute(
        "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM entry_tags)")


def set_entry_tags(connection, entry_id, tags):
    """Replace an entry's tags (rules applied again here) and tidy unused tags."""
    connection.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
    for name in normalize_tags(tags):
        connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        connection.execute(
            "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) "
            "SELECT ?, id FROM tags WHERE name = ?", (entry_id, name))
    delete_unused_tags(connection)


def get_tags_for_entries(connection, ids):
    """{entry_id: [tag names in the order they were added]} for the given ids."""
    ids = list(dict.fromkeys(ids))
    result = {entry_id: [] for entry_id in ids}
    # Stay well below SQLite's limit on query parameters.
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = connection.execute(f"""
            SELECT entry_tags.entry_id, tags.name
            FROM entry_tags JOIN tags ON tags.id = entry_tags.tag_id
            WHERE entry_tags.entry_id IN ({", ".join("?" for _ in chunk)})
            ORDER BY entry_tags.rowid
        """, chunk).fetchall()
        for entry_id, name in rows:
            result[entry_id].append(name)
    return result


def all_tags_with_counts(connection):
    """[{"name": ..., "count": ...}] for every tag on a live (not trashed) entry."""
    rows = connection.execute("""
        SELECT tags.name, COUNT(entry_tags.entry_id)
        FROM tags
        JOIN entry_tags ON entry_tags.tag_id = tags.id
        JOIN live_entries ON live_entries.id = entry_tags.entry_id
        GROUP BY tags.id ORDER BY tags.name
    """).fetchall()
    return [{"name": name, "count": count} for name, count in rows]
