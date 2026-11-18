"""Shared test helpers: test databases, test users, logging a client in.

Every test module imports this before the app, so the settings below apply
to the whole run.

Databases: tests run on throwaway SQLite files. Run with TEST_DB=postgres
(and TEST_DATABASE_URL set, in the environment or .env) to run the same
tests on PostgreSQL instead: each test then gets its own new schema, which
is dropped afterwards. Tests about SQLite itself (file backups, upgrading old
journal files) are skipped there.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import quote
import uuid

# Never let a developer's .env point the tests at a real database, and never
# let the app's startup migration touch the real journal.db. (python-dotenv
# doesn't override variables that are already set, even to "".)
os.environ['DATABASE_URL'] = ''
os.environ['APP_ENV'] = 'development'
_SESSION_FOLDER = tempfile.mkdtemp(prefix='journal-tests-')
os.environ['DATABASE'] = os.path.join(_SESSION_FOLDER, 'default.db')
os.environ.pop('JOURNAL_DATABASE', None)
# Run like a production server: on UTC, whatever this computer's zone is. Test
# users without a time zone are UTC users too, so a test's own date.today()
# matches the app's "today" for them.
os.environ['TZ'] = 'UTC'
if hasattr(time, 'tzset'):
    time.tzset()

from flask_login.utils import _create_identifier  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from database import close_open_connections, connect, initialize_database  # noqa: E402


def _test_database_url():
    url = os.environ.get('TEST_DATABASE_URL', '').strip()
    if not url:
        try:
            from dotenv import dotenv_values
            url = (dotenv_values(Path(__file__).resolve().parent.parent / '.env')
                   .get('TEST_DATABASE_URL') or '').strip()
        except ImportError:
            url = ''
    return url


POSTGRES = os.environ.get('TEST_DB', '').strip().lower() in ('postgres', 'postgresql')
TEST_DATABASE_URL = _test_database_url()
if POSTGRES and not TEST_DATABASE_URL:
    raise RuntimeError('TEST_DB=postgres needs TEST_DATABASE_URL (in the environment or .env).')

# For tests that are about SQLite itself.
sqlite_only = unittest.skipIf(POSTGRES, 'SQLite-only test')
postgres_only = unittest.skipUnless(POSTGRES, 'needs TEST_DB=postgres and TEST_DATABASE_URL')


def with_search_path(url, schema):
    """The URL with every connection's search_path set to `schema`."""
    option = 'options=' + quote(f'-c search_path={schema}', safe='')
    return url + ('&' if '?' in url else '?') + option


def admin_connection():
    """A connection to the test PostgreSQL database itself (no test schema)."""
    import psycopg
    return psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=10)


def _drop_schema(schema):
    close_open_connections()   # an open transaction would block DROP SCHEMA
    with admin_connection() as admin:
        admin.execute("SET lock_timeout = '10s'")
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def empty_test_database(testcase, name='journal.db'):
    """A brand-new database with no tables yet. Removed after the test."""
    if POSTGRES:
        schema = 'journal_test_' + uuid.uuid4().hex[:16]
        with admin_connection() as admin:
            admin.execute(f'CREATE SCHEMA "{schema}"')
        testcase.addCleanup(_drop_schema, schema)
        return with_search_path(TEST_DATABASE_URL, schema)
    folder = tempfile.mkdtemp(prefix='journal-test-', dir=_SESSION_FOLDER)
    testcase.addCleanup(shutil.rmtree, folder, ignore_errors=True)
    return os.path.join(folder, name)


def freeze_time(testcase, moment):
    """Make timeutil's clock return `moment` for the rest of the test.

    `moment` is a datetime: naive means UTC. Every part of the app (user
    time and system time) reads the clock through timeutil.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    patcher = mock.patch('timeutil.clock', return_value=moment)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return moment


def skip_daily_maintenance(app_module, target, day=None):
    """Mark today's backup and Trash cleanup as done for `target`, so no
    request in the test runs them."""
    app_module._daily_backups_checked.add(app_module.maintenance_key(target, day))


def new_test_database(testcase, name='journal.db'):
    """A fresh database with the current schema, for one test. Returns its
    target (a SQLite path, or a PostgreSQL URL for a private schema)."""
    target = empty_test_database(testcase, name)
    initialize_database(target)
    return target

TEST_EMAIL = 'me@example.com'
TEST_PASSWORD = 'correct-horse-battery'

# Most tests post forms without CSRF tokens and log in many times a minute.
# tests/test_security.py keeps both protections on.
SECURITY_OFF = {'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False}

# A deliberately cheap hash so creating test users stays fast. The app itself
# uses werkzeug's default (scrypt); check_password_hash accepts both.
_hashes = {}


def add_user(path, email=TEST_EMAIL, password=TEST_PASSWORD, display_name=None, timezone=None):
    """Insert a user straight into the database at `path`. Returns its id.
    No time zone (the default) behaves as UTC."""
    if password not in _hashes:
        _hashes[password] = generate_password_hash(password, method='pbkdf2:sha256:1000')
    db = connect(path)
    try:
        user_id = db.insert(
            'INSERT INTO users (email, password_hash, created_at, display_name, timezone) '
            'VALUES (?, ?, ?, ?, ?)',
            (email, _hashes[password], datetime.now().isoformat(), display_name, timezone))
        db.commit()
        return user_id
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
