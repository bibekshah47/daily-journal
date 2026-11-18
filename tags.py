"""Tag rules shared by the forms, History filter and backup restore.

Each user has their own tags. A tag is lowercase letters, numbers, "-" and "_"
only, at most MAX_TAG_LENGTH characters. An entry has at most MAX_TAGS_PER_ENTRY tags and no duplicates.
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


def delete_unused_tags(connection, user_id=None):
    """Remove tags no entry uses: one user's, or (user_id None) everyone's.

    Only the daily Trash cleanup passes None; an unused tag holds no data.
    """
    if user_id is None:
        connection.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM entry_tags)")
    else:
        connection.execute(
            "DELETE FROM tags WHERE user_id = ? AND id NOT IN (SELECT tag_id FROM entry_tags)",
            (user_id,))


def set_entry_tags(connection, entry_id, tags, *, user_id):
    """Replace one of user_id's entries' tags (rules applied again here) and tidy unused tags.

    Does nothing to an entry that belongs to someone else, and only ever links
    an entry to tags owned by the same user.
    """
    if user_id is None:
        raise ValueError("Tags need an owner.")
    connection.execute(
        "DELETE FROM entry_tags WHERE entry_id = ? "
        "AND entry_id IN (SELECT id FROM entries WHERE user_id = ?)", (entry_id, user_id))
    for position, name in enumerate(normalize_tags(tags)):
        connection.execute(
            "INSERT INTO tags (user_id, name) "
            "SELECT ?, ? WHERE EXISTS (SELECT 1 FROM entries WHERE id = ? AND user_id = ?) "
            "ON CONFLICT DO NOTHING",
            (user_id, name, entry_id, user_id))
        connection.execute(
            "INSERT INTO entry_tags (entry_id, tag_id, sort_order) "
            "SELECT entries.id, tags.id, ? FROM entries "
            "JOIN tags ON tags.user_id = entries.user_id "
            "WHERE entries.id = ? AND entries.user_id = ? AND tags.name = ? "
            "ON CONFLICT DO NOTHING",
            (position, entry_id, user_id, name))
    delete_unused_tags(connection, user_id)


def get_tags_for_entries(connection, ids, *, user_id):
    """{entry_id: [tag names in the order they were added]} for user_id's entries."""
    ids = list(dict.fromkeys(ids))
    result = {entry_id: [] for entry_id in ids}
    # Stay well below either database's limit on query parameters.
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = connection.execute(f"""
            SELECT entry_tags.entry_id, tags.name
            FROM entry_tags
            JOIN tags ON tags.id = entry_tags.tag_id
            JOIN entries ON entries.id = entry_tags.entry_id
            WHERE tags.user_id = ? AND entries.user_id = ?
              AND entry_tags.entry_id IN ({", ".join("?" for _ in chunk)})
            ORDER BY entry_tags.entry_id, entry_tags.sort_order, entry_tags.tag_id
        """, (user_id, user_id, *chunk)).fetchall()
        for entry_id, name in rows:
            result[entry_id].append(name)
    return result


def all_tags_with_counts(connection, *, user_id):
    """[{"name": ..., "count": ...}] for user_id's tags on live (not trashed) entries."""
    rows = connection.execute("""
        SELECT tags.name, COUNT(entry_tags.entry_id)
        FROM tags
        JOIN entry_tags ON entry_tags.tag_id = tags.id
        JOIN live_entries ON live_entries.id = entry_tags.entry_id
        WHERE tags.user_id = ? AND live_entries.user_id = ?
        GROUP BY tags.id, tags.name
    """, (user_id, user_id)).fetchall()
    # Sorted here: PostgreSQL's text order depends on the server's locale.
    return [{"name": name, "count": count} for name, count in sorted(rows)]
