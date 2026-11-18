"""Settings from the environment, and the per-request database connection."""
from datetime import date
import importlib
import os
from pathlib import Path
import unittest

from flask import g

import config
from config import ConfigError, DEFAULT_SECRET_KEY, load_config
from database import is_postgres
from tests.helpers import POSTGRES, SECURITY_OFF, new_test_database, signed_in_client

NEON = 'postgresql://user:pw@ep-cool-name.eu-central-1.aws.neon.tech/journal?sslmode=require'


class ConfigTests(unittest.TestCase):
    def test_local_defaults(self):
        settings = load_config({})
        self.assertEqual(settings['APP_ENV'], 'development')
        self.assertEqual(settings['SECRET_KEY'], DEFAULT_SECRET_KEY)
        self.assertEqual(settings['DATABASE'], 'journal.db')
        self.assertEqual(settings['MAX_CONTENT_LENGTH'], 10 * 1024 * 1024)

    def test_values_from_the_environment(self):
        settings = load_config({'APP_ENV': ' Production ', 'SECRET_KEY': 'a' * 64,
                                'DATABASE_URL': f'  {NEON} '})
        self.assertEqual(settings['APP_ENV'], 'production')
        self.assertEqual(settings['SECRET_KEY'], 'a' * 64)
        self.assertEqual(settings['DATABASE'], NEON)
        self.assertEqual(load_config({'DATABASE': '/data/journal.db'})['DATABASE'], '/data/journal.db')

    def test_database_url_chooses_postgresql(self):
        for url in (NEON, 'postgres://u@localhost/journal'):
            with self.subTest(url=url):
                settings = load_config({'DATABASE_URL': url, 'DATABASE': 'ignored.db'})
                self.assertEqual(settings['DATABASE'], url)
                self.assertTrue(is_postgres(settings['DATABASE']))
        self.assertFalse(is_postgres(load_config({'DATABASE_URL': '  '})['DATABASE']))
        with self.assertRaisesRegex(ConfigError, 'DATABASE_URL must start with postgres'):
            load_config({'DATABASE_URL': 'mysql://u@localhost/journal'})

    def test_production_refuses_to_start_without_database_url(self):
        with self.assertRaisesRegex(ConfigError, 'DATABASE_URL must be set in production'):
            load_config({'APP_ENV': 'production', 'SECRET_KEY': 'a' * 64,
                         'DATABASE': '/data/journal.db'})

    def test_database_falls_back_to_journal_database(self):
        self.assertEqual(load_config({'JOURNAL_DATABASE': 'old.db'})['DATABASE'], 'old.db')
        self.assertEqual(load_config({'JOURNAL_DATABASE': 'old.db', 'DATABASE': 'new.db'})['DATABASE'],
                         'new.db')
        self.assertEqual(load_config({'DATABASE': '  '})['DATABASE'], 'journal.db')

    def test_production_refuses_a_missing_or_default_secret_key(self):
        for secret in (None, '', '   ', DEFAULT_SECRET_KEY):
            with self.subTest(secret=secret):
                environ = {'APP_ENV': 'production'}
                if secret is not None:
                    environ['SECRET_KEY'] = secret
                with self.assertRaisesRegex(ConfigError, 'SECRET_KEY'):
                    load_config(environ)

    def test_development_accepts_the_default_secret_key(self):
        self.assertEqual(load_config({'APP_ENV': 'development', 'SECRET_KEY': DEFAULT_SECRET_KEY})
                         ['SECRET_KEY'], DEFAULT_SECRET_KEY)

    def test_unknown_app_env_is_refused(self):
        with self.assertRaisesRegex(ConfigError, 'APP_ENV'):
            load_config({'APP_ENV': 'staging'})

    def test_create_app_refuses_to_start_in_production_without_a_key_or_database(self):
        module = importlib.import_module('app')
        names = ('APP_ENV', 'SECRET_KEY', 'DATABASE_URL')
        saved = {name: os.environ.get(name) for name in names}
        try:
            os.environ['APP_ENV'] = 'production'
            os.environ['DATABASE_URL'] = NEON
            os.environ.pop('SECRET_KEY', None)
            with self.assertRaisesRegex(ConfigError, 'SECRET_KEY'):
                module.create_app()
            os.environ['SECRET_KEY'] = 'b' * 64
            os.environ['DATABASE_URL'] = ''
            with self.assertRaisesRegex(ConfigError, 'DATABASE_URL'):
                module.create_app()
            # Both set: it starts. (A test database stands in for the URL.)
            os.environ['DATABASE_URL'] = NEON
            app = module.create_app({'DATABASE': new_test_database(self)})
            self.assertEqual(app.config['APP_ENV'], 'production')
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_env_example_lists_every_setting(self):
        example = (Path(config.__file__).parent / '.env.example').read_text()
        for name in ('APP_ENV=', 'SECRET_KEY=', 'DATABASE=', 'DATABASE_URL=', 'TEST_DATABASE_URL='):
            self.assertIn(name, example)


class RequestConnectionTests(unittest.TestCase):
    def setUp(self):
        self.path = new_test_database(self)
        module = importlib.import_module('app')
        self.app = module.create_app({'TESTING': True, 'DATABASE': self.path, **SECURITY_OFF})
        module._daily_backups_checked.add((self.path if POSTGRES else os.path.abspath(self.path),
                                           date.today()))

    def test_one_connection_per_request_closed_at_the_end(self):
        from db import get_db
        with self.app.app_context():
            connection = get_db()
            self.assertIs(get_db(), connection)
            self.assertIs(g.db, connection)
            self.assertEqual(connection.backend, 'postgres' if POSTGRES else 'sqlite')
            row = connection.fetchone('SELECT 1 AS one, 2 AS two')
            self.assertEqual((row[0], row['two'], dict(row)), (1, 2, {'one': 1, 'two': 2}))
            self.assertEqual(row, (1, 2))
            if not POSTGRES:
                self.assertEqual(connection.fetchone('PRAGMA foreign_keys')[0], 1)
        with self.assertRaises(Exception):   # closed by teardown
            connection.execute('SELECT 1')
        with self.app.app_context():
            self.assertIsNot(get_db(), connection)

    def test_uncommitted_changes_are_rolled_back(self):
        from db import get_db
        with self.app.app_context():
            get_db().execute("INSERT INTO entries (content, created_at) VALUES ('x', '2026-01-01')")
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM entries').fetchone()[0], 0)

    def test_every_page_loads_with_the_request_connection(self):
        client, _ = signed_in_client(self.app, self.path)
        client.post('/write', data={'journal': 'Hello', 'tags': 'one'})
        for url in ('/', '/write', '/history', '/entry/1', '/edit/1', '/insights',
                    '/week', '/trash', '/backup'):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)


if __name__ == '__main__':
    unittest.main()
