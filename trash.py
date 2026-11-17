"""Trash: soft-deleted entries are erased for good after TRASH_DAYS.

Only entries already in Trash are ever erased here. Their tag links go with
them (foreign keys cascade) and tags nothing uses any more are removed.
None of these functions commit: callers own the transaction.
"""
from datetime import timedelta

from tags import delete_unused_tags

TRASH_DAYS = 30


def _erase(connection, condition="", parameters=()):
    cursor = connection.execute(
        f"DELETE FROM entries WHERE deleted_at IS NOT NULL {condition}", parameters)
    delete_unused_tags(connection)
    return cursor.rowcount


def purge_entry(connection, entry_id):
    """Erase one trashed entry. Returns 1, or 0 if it wasn't in Trash."""
    return _erase(connection, "AND id = ?", (entry_id,))


def empty_trash(connection):
    """Erase every trashed entry. Returns how many were erased."""
    return _erase(connection)


def cleanup_trash(connection, now, days=TRASH_DAYS):
    """Erase entries that have been in Trash for more than `days`. Returns the count."""
    cutoff = (now - timedelta(days=days)).isoformat()
    return _erase(connection, "AND deleted_at < ?", (cutoff,))
