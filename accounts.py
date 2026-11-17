"""User accounts: email rules, password hashing, and claiming old entries.

Passwords are only ever passed to werkzeug's hashing functions; they are
never stored, logged or echoed back into a page.
"""
from datetime import datetime
import re
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 40

# Deliberately simple: something@something.something, no spaces.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SPACES = re.compile(r"\s+")

# Checked when an email isn't found, so a wrong email takes as long as a
# wrong password and response times don't reveal which accounts exist.
_dummy_hash = None


class EmailTaken(ValueError):
    """An account with this email already exists."""


def normalize_email(email):
    return str(email or "").strip().lower()


def normalize_display_name(name):
    """Trimmed, with runs of spaces or line breaks made one space. "" -> None."""
    name = _SPACES.sub(" ", str(name or "")).strip()
    return name or None


def first_name(display_name):
    """The first word of a display name, for the greeting: "Bibek Shah" -> "Bibek".

    None for a missing or blank name.
    """
    words = str(display_name or "").split()
    return words[0] if words else None


def display_name_error(name):
    """None, or the message to show next to the name field (name already normalized)."""
    if name and len(name) > MAX_DISPLAY_NAME_LENGTH:
        return f"Use {MAX_DISPLAY_NAME_LENGTH} characters or fewer"
    return None


def email_error(email):
    """None, or the message to show next to the email field."""
    if not email or len(email) > MAX_EMAIL_LENGTH or not _EMAIL.match(email):
        return "Enter a valid email"
    return None


def password_error(password):
    """None, or the message to show next to the password field."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use {MAX_PASSWORD_LENGTH} characters or fewer"
    return None


def create_user(connection, email, password, display_name=None, now=None):
    """Insert a user (email and name already normalized and checked). Returns the new id.

    Raises EmailTaken if the email is in use. Does not commit.
    """
    try:
        cursor = connection.execute(
            "INSERT INTO users (email, password_hash, created_at, display_name) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(password), (now or datetime.now()).isoformat(),
             display_name))
    except sqlite3.IntegrityError as error:
        raise EmailTaken(email) from error
    return cursor.lastrowid


def find_user(connection, user_id):
    """(id, email, display_name) for a user id, or None."""
    return connection.execute(
        "SELECT id, email, display_name FROM users WHERE id = ?", (user_id,)).fetchone()


def find_user_by_email(connection, email):
    return connection.execute(
        "SELECT id, email FROM users WHERE email = ?", (normalize_email(email),)).fetchone()


def authenticate(connection, email, password):
    """(id, email, display_name) if the email and password match an account, else None."""
    global _dummy_hash
    if len(password) > MAX_PASSWORD_LENGTH:
        return None   # never spend time hashing absurdly long input
    row = connection.execute(
        "SELECT id, email, password_hash, display_name FROM users WHERE email = ?",
        (normalize_email(email),)).fetchone()
    if row is None:
        if _dummy_hash is None:
            _dummy_hash = generate_password_hash("not-a-real-password")
        check_password_hash(_dummy_hash, password)
        return None
    if not check_password_hash(row[2], password):
        return None
    return row[0], row[1], row[3]


def assign_unowned(connection, user_id):
    """Give every entry and tag that has no owner to user_id.

    Rows that already have an owner are never touched. If the user already
    has a tag with the same name as an unowned one, the two are merged.
    Returns (entries moved, tags moved). Commits.
    """
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        clashes = connection.execute("""
            SELECT old.id, mine.id FROM tags AS old
            JOIN tags AS mine ON mine.name = old.name AND mine.user_id = ?
            WHERE old.user_id IS NULL
        """, (user_id,)).fetchall()
        for old_id, mine_id in clashes:
            connection.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) "
                "SELECT entry_id, ? FROM entry_tags WHERE tag_id = ?", (mine_id, old_id))
            connection.execute(
                "DELETE FROM tags WHERE id = ? AND user_id IS NULL", (old_id,))
        tags = connection.execute(
            "UPDATE tags SET user_id = ? WHERE user_id IS NULL", (user_id,)).rowcount
        entries = connection.execute(
            "UPDATE entries SET user_id = ? WHERE user_id IS NULL", (user_id,)).rowcount
    return entries, tags + len(clashes)
