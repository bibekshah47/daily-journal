"""Trash: soft-deleted entries are erased for good after TRASH_DAYS.

Only entries already in Trash are ever erased here. Their tag links go with
them (foreign keys cascade) and tags nothing uses any more are removed.
None of these functions commit: callers own the transaction.
"""
from datetime import timedelta

from tags import delete_unused_tags

TRASH_DAYS = 30


def _erase(connection, condition, parameters=()):
    # `condition` is always one of the fixed strings below; values go in `parameters`.
    cursor = connection.execute(
        f"DELETE FROM entries WHERE deleted_at IS NOT NULL {condition}", parameters)
    delete_unused_tags(connection)
    return cursor.rowcount


def purge_entry(connection, entry_id, *, user_id):
    """Erase one of user_id's trashed entries. Returns 1, or 0 if it wasn't in their Trash."""
    return _erase(connection, "AND id = ? AND user_id = ?", (entry_id, user_id))


def empty_trash(connection, *, user_id):
    """Erase every entry in user_id's Trash. Returns how many were erased."""
    return _erase(connection, "AND user_id = ?", (user_id,))


# deleted_at is the owner's wall-clock time. In a zone behind UTC (as far as
# 12 hours, e.g. Hawaii at -10) that reads older than the real moment, so a
# plain UTC comparison could erase an entry up to 12 hours early. The cleanup
# allows one extra day, so nothing is ever erased before its 30 days are up in
# its owner's time zone (it may stay up to a day longer instead).
TIME_ZONE_GRACE = timedelta(days=1)


def cleanup_trash(connection, utc_now, days=TRASH_DAYS):
    """Erase entries that have been in Trash for more than `days` (plus the
    time-zone grace day). `utc_now` is system time. Returns the count.

    Daily maintenance for every user at once; it never returns anyone's data.
    """
    cutoff = (utc_now - timedelta(days=days) - TIME_ZONE_GRACE).isoformat()
    return _erase(connection, "AND deleted_at < ?", (cutoff,))
