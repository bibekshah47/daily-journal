"""Shared test helpers: test users and logging a test client in."""
from datetime import datetime

from flask_login.utils import _create_identifier
from werkzeug.security import generate_password_hash

from database import connect

TEST_EMAIL = 'me@example.com'
TEST_PASSWORD = 'correct-horse-battery'

# Most tests post forms without CSRF tokens and log in many times a minute.
# tests/test_security.py keeps both protections on.
SECURITY_OFF = {'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False}

# A deliberately cheap hash so creating test users stays fast. The app itself
# uses werkzeug's default (scrypt); check_password_hash accepts both.
_hashes = {}


def add_user(path, email=TEST_EMAIL, password=TEST_PASSWORD, display_name=None):
    """Insert a user straight into the database at `path`. Returns its id."""
    if password not in _hashes:
        _hashes[password] = generate_password_hash(password, method='pbkdf2:sha256:1000')
    db = connect(path)
    try:
        cursor = db.execute(
            'INSERT INTO users (email, password_hash, created_at, display_name) VALUES (?, ?, ?, ?)',
            (email, _hashes[password], datetime.now().isoformat(), display_name))
        db.commit()
        return cursor.lastrowid
    finally:
        db.close()


def log_in_as(client, user_id, session_version=0):
    """Log a test client in without making a request (so no daily-backup hook runs).

    These are the session keys Flask-Login itself writes in login_user(),
    including _id, which "strong" session protection checks on every request.
    """
    with client.application.test_request_context(environ_base=client.environ_base):
        identifier = _create_identifier()
    with client.session_transaction() as session:
        session['_user_id'] = f'{user_id}:{session_version}'   # User.get_id()
        session['_fresh'] = True
        session['_id'] = identifier


def signed_in_client(app, path, email=TEST_EMAIL):
    """A new test client logged in as a new user in the database at `path`.

    Returns (client, user_id).
    """
    user_id = add_user(path, email)
    client = app.test_client()
    log_in_as(client, user_id)
    return client, user_id
