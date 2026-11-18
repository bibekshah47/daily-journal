"""User accounts: email rules, password hashing, and claiming old entries.

Passwords are only ever passed to werkzeug's hashing functions; they are
never stored, logged or echoed back into a page.
"""
from datetime import datetime
import re

from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from database import IntegrityError

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 40

# Deliberately simple: something@something.something, no spaces.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SPACES = re.compile(r"\s+")

# Password reset links: signed with SECRET_KEY, valid for one hour, and only
# while the account's session_version is unchanged (so each link works once).
RESET_SALT = "password-reset"
RESET_MAX_AGE = 3600

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
    created = (now or datetime.now()).isoformat()
    try:
        return connection.insert(
            "INSERT INTO users (email, password_hash, created_at, display_name, password_changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, generate_password_hash(password), created, display_name, created))
    except IntegrityError as error:
        # On PostgreSQL the transaction is now aborted: the caller rolls back.
        raise EmailTaken(email) from error


def find_user(connection, user_id):
    """(id, email, display_name, session_version) for a user id, or None."""
    return connection.execute(
        "SELECT id, email, display_name, session_version FROM users WHERE id = ?",
        (user_id,)).fetchone()


def find_user_by_email(connection, email):
    """(id, email, display_name, session_version) for an email, or None."""
    return connection.execute(
        "SELECT id, email, display_name, session_version FROM users WHERE email = ?",
        (normalize_email(email),)).fetchone()


def authenticate(connection, email, password):
    """(id, email, display_name, session_version) if the email and password
    match an account, else None."""
    global _dummy_hash
    if len(password) > MAX_PASSWORD_LENGTH:
        return None   # never spend time hashing absurdly long input
    row = connection.execute(
        "SELECT id, email, password_hash, display_name, session_version FROM users WHERE email = ?",
        (normalize_email(email),)).fetchone()
    if row is None:
        if _dummy_hash is None:
            _dummy_hash = generate_password_hash("not-a-real-password")
        check_password_hash(_dummy_hash, password)
        return None
    if not check_password_hash(row[2], password):
        return None
    return row[0], row[1], row[3], row[4]


# ---------- Settings ----------

def check_password(connection, user_id, password):
    """True if `password` is this user's current password."""
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    row = connection.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return row is not None and check_password_hash(row[0], password)


def change_display_name(connection, user_id, display_name):
    """Set the (normalized, checked) display name; None removes it. Commits."""
    with connection:
        connection.execute("UPDATE users SET display_name = ? WHERE id = ?",
                           (display_name, user_id))


def change_email(connection, user_id, email):
    """Set the (normalized, checked) email. Raises EmailTaken. Commits."""
    try:
        with connection:
            connection.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
    except IntegrityError as error:
        raise EmailTaken(email) from error


def password_changed_at(connection, user_id):
    """When the password was last set (ISO text), or None if not recorded."""
    row = connection.execute(
        "SELECT password_changed_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def set_password(connection, user_id, password, now=None):
    """Store a new password and end every session and reset link.

    Increases session_version, so logins (including "Keep me logged in"
    cookies) made before this stop working. Returns the new version. Commits.
    """
    with connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, session_version = session_version + 1, "
            "password_changed_at = ? WHERE id = ?",
            (generate_password_hash(password), (now or datetime.now()).isoformat(), user_id))
        return connection.execute(
            "SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()[0]


def delete_user(connection, user_id):
    """Erase the account and every entry, tag and tag link it owns. Commits.

    The foreign keys cascade from users anyway; deleting entries and tags
    explicitly as well keeps this complete on databases upgraded from older
    schemas.
    """
    with connection:
        connection.execute("DELETE FROM entries WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM tags WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------- Password reset links ----------

def _reset_serializer(secret_key):
    return URLSafeTimedSerializer(secret_key, salt=RESET_SALT)


def make_reset_token(secret_key, user_id, session_version):
    return _reset_serializer(secret_key).dumps(
        {"user_id": user_id, "session_version": session_version})


def user_for_reset_token(connection, secret_key, token, max_age=RESET_MAX_AGE):
    """The user row a reset token is for, or None if the token is invalid,
    expired, tampered with, or already used (session_version changed)."""
    try:
        data = _reset_serializer(secret_key).loads(token, max_age=max_age)
    except BadData:
        return None
    if not isinstance(data, dict):
        return None
    user_id, version = data.get("user_id"), data.get("session_version")
    if type(user_id) is not int or type(version) is not int:
        return None
    row = find_user(connection, user_id)
    if row is None or row[3] != version:
        return None
    return row


def assign_unowned(connection, user_id):
    """Give every entry and tag that has no owner to user_id.

    Rows that already have an owner are never touched. If the user already
    has a tag with the same name as an unowned one, the two are merged.
    Returns (entries moved, tags moved). Commits.
    """
    with connection:
        clashes = connection.execute("""
            SELECT old.id, mine.id FROM tags AS old
            JOIN tags AS mine ON mine.name = old.name AND mine.user_id = ?
            WHERE old.user_id IS NULL
        """, (user_id,)).fetchall()
        for old_id, mine_id in clashes:
            connection.execute(
                "INSERT INTO entry_tags (entry_id, tag_id, sort_order) "
                "SELECT entry_id, ?, sort_order FROM entry_tags WHERE tag_id = ? "
                "ON CONFLICT DO NOTHING", (mine_id, old_id))
            connection.execute(
                "DELETE FROM tags WHERE id = ? AND user_id IS NULL", (old_id,))
        tags = connection.execute(
            "UPDATE tags SET user_id = ? WHERE user_id IS NULL", (user_id,)).rowcount
        entries = connection.execute(
            "UPDATE entries SET user_id = ? WHERE user_id IS NULL", (user_id,)).rowcount
    return entries, tags + len(clashes)
