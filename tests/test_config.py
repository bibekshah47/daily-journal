"""Settings from the environment, and the per-request database connection."""
from datetime import date
import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from flask import g

import config
from config import ConfigError, DEFAULT_SECRET_KEY, load_config
from database import initialize_database


class ConfigTests(unittest.TestCase):
    def test_local_defaults(self):
        settings = load_config({})
        self.assertEqual(settings['APP_ENV'], 'development')
        self.assertEqual(settings['SECRET_KEY'], DEFAULT_SECRET_KEY)
        self.assertEqual(settings['DATABASE'], 'journal.db')
        self.assertEqual(settings['MAX_CONTENT_LENGTH'], 10 * 1024 * 1024)

    def test_values_from_the_environment(self):
        settings = load_config({'APP_ENV': ' Production ', 'SECRET_KEY': 'a' * 64,
                                'DATABASE': '/data/journal.db'})
        self.assertEqual(settings['APP_ENV'], 'production')
        self.assertEqual(settings['SECRET_KEY'], 'a' * 64)
        self.assertEqual(settings['DATABASE'], '/data/journal.db')

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

    def test_create_app_refuses_to_start_in_production_without_a_key(self):
        module = importlib.import_module('app')
        saved = {name: os.environ.get(name) for name in ('APP_ENV', 'SECRET_KEY')}
        try:
            os.environ['APP_ENV'] = 'production'
            os.environ.pop('SECRET_KEY', None)
            with self.assertRaises(ConfigError):
                module.create_app()
            os.environ['SECRET_KEY'] = 'b' * 64
            self.assertEqual(module.create_app().config['APP_ENV'], 'production')
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_env_example_lists_every_setting(self):
        example = (Path(config.__file__).parent / '.env.example').read_text()
        for name in ('APP_ENV=', 'SECRET_KEY=', 'DATABASE='):
            self.assertIn(name, example)


class RequestConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'journal.db')
        initialize_database(self.path)
        module = importlib.import_module('app')
        self.app = module.create_app({'TESTING': True, 'DATABASE': self.path})
        module._daily_backups_checked.add((os.path.abspath(self.path), date.today()))

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_connection_per_request_closed_at_the_end(self):
        from db import get_db
        with self.app.app_context():
            connection = get_db()
            self.assertIs(get_db(), connection)
            self.assertIs(g.db, connection)
            self.assertIs(connection.row_factory, sqlite3.Row)
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):   # closed by teardown
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
        client = self.app.test_client()
        client.post('/write', data={'journal': 'Hello', 'tags': 'one'})
        for url in ('/', '/write', '/history', '/entry/1', '/edit/1', '/insights',
                    '/week', '/trash', '/backup'):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)


if __name__ == '__main__':
    unittest.main()
