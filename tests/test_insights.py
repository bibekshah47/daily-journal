"""Insights calculations and page integration, using temporary SQLite records."""
from datetime import date
import hashlib
import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from database import initialize_database
from journal_insights import get_journal_stats
from tests.helpers import signed_in_client


class InsightsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE entries (created_at TEXT, went_well TEXT, went_wrong TEXT, improve_tomorrow TEXT, deleted_at TEXT, user_id INTEGER)')
        # Stats read the same view the app uses, which hides entries in Trash.
        self.db.execute('CREATE VIEW live_entries AS SELECT * FROM entries WHERE deleted_at IS NULL')

    def tearDown(self):
        self.db.close()

    def add(self, day, *answers, user_id=1):
        answers = list(answers) + [None] * (3 - len(answers))
        self.db.execute('INSERT INTO entries (created_at, went_well, went_wrong, improve_tomorrow, user_id) '
                        'VALUES (?,?,?,?,?)', (day, *answers, user_id))

    def stats(self, today=date(2026, 11, 13), user_id=1):
        return get_journal_stats(self.db, today, user_id=user_id)

    def test_only_counts_one_users_entries(self):
        self.add('2026-11-13', 'Mine')
        for day in ['2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']:
            self.add(day, 'Theirs', user_id=2)
        self.add('2026-11-13', 'Nobody owns this', user_id=None)
        stats = self.stats()
        self.assertEqual((stats['total_entries'], stats['current_streak'], stats['reflection_entries']), (1, 1, 1))
        self.assertEqual(self.stats(user_id=2)['current_streak'], 4)
        self.assertEqual(self.stats(user_id=3)['total_entries'], 0)

    def test_empty(self):
        stats = self.stats()
        for name in ['total_entries','monthly_entries','current_streak','longest_streak','reflection_entries','reflection_rate','top_question_count']:
            self.assertEqual(stats[name], 0)
        self.assertEqual(stats['top_questions'], [])

    def test_unique_dates_and_historical_longest(self):
        for day in ['2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-11-12','2026-11-13','2026-11-13']:
            self.add(day)
        stats = self.stats()
        self.assertEqual(stats['total_entries'], 7)
        self.assertEqual(stats['monthly_entries'], 3)
        self.assertEqual(stats['current_streak'], 2)
        self.assertEqual(stats['longest_streak'], 4)

    def test_yesterday_and_broken_streak(self):
        self.add('2026-11-11'); self.add('2026-11-12')
        self.assertEqual(self.stats()['current_streak'], 2)
        self.assertEqual(self.stats(date(2026,11,14))['current_streak'], 0)

    def test_month_and_year_boundaries(self):
        for day in ['2025-12-30','2025-12-31','2026-01-01','2026-01-01']:
            self.add(day)
        stats = self.stats(date(2026,1,1))
        self.assertEqual(stats['current_streak'], 3)
        self.assertEqual(stats['longest_streak'], 3)
        self.assertEqual(stats['monthly_entries'], 2)

    def test_whitespace_reflection_rate_and_ties(self):
        self.add('2026-11-13', 'Win', 'Challenge', None)
        self.add('2026-11-13', '\t\n ', None, '  ')
        self.add('2026-11-12', None, None, None)
        stats = self.stats()
        self.assertEqual(stats['reflection_entries'], 1)
        self.assertAlmostEqual(stats['reflection_rate'], 100 / 3)
        self.assertEqual(stats['top_question_count'], 1)
        self.assertEqual(stats['top_questions'], ['What went well today?', "What didn't go well today?"])
        self.add('2026-11-11', 'Another win')
        self.assertEqual(self.stats()['top_questions'], ['What went well today?'])
        self.assertEqual(self.stats()['reflection_rate'], 50)

    def test_invalid_dates_do_not_break_stats(self):
        self.add('invalid', 'Still counts')
        stats = self.stats()
        self.assertEqual(stats['total_entries'], 1)
        self.assertEqual(stats['reflection_entries'], 1)
        self.assertEqual(stats['longest_streak'], 0)

    def test_page_sidebar_links_and_shared_home_stats(self):
        real = Path('journal.db')
        digest = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'test.db')
            initialize_database(path)
            previous_env = os.environ.get('JOURNAL_DATABASE')
            os.environ['JOURNAL_DATABASE'] = path
            app = importlib.import_module('app').app
            previous_config = app.config['DATABASE']
            app.config.update(TESTING=True, DATABASE=path)
            try:
                client, user_id = signed_in_client(app, path)
                page = client.get('/insights')
                self.assertEqual(page.status_code, 200)
                self.assertIn('Write your first entry', page.text)
                self.assertIn('No reflections yet', page.text)
                self.assertIn('0.0%', page.text)
                self.assertIn('aria-current="page"', page.text)
                response = client.post('/write', data={'journal':'Body','went_well':'Win'})
                entry_id = int(response.location.split('/entry/')[1].split('?')[0])
                for url in ['/','/write','/history',f'/edit/{entry_id}',f'/entry/{entry_id}','/insights']:
                    result = client.get(url)
                    self.assertEqual(result.status_code, 200)
                    self.assertIn('href="/insights"', result.text)
                page = client.get('/insights').text
                self.assertIn('100.0%', page)
                self.assertIn('What went well today?', page)
                self.assertNotIn('No reflections yet', page)
                self.assertNotIn('chart.js', page.lower())
                with sqlite3.connect(path) as db:
                    db.row_factory = sqlite3.Row
                    stats = get_journal_stats(db, user_id=user_id)
                    self.assertEqual(stats['total_entries'], 1)
                    self.assertEqual(stats['current_streak'], 1)
                    self.assertEqual(stats['monthly_entries'], 1)
            finally:
                app.config['DATABASE'] = previous_config
                if previous_env is None: os.environ.pop('JOURNAL_DATABASE', None)
                else: os.environ['JOURNAL_DATABASE'] = previous_env
        if digest:
            self.assertEqual(hashlib.sha256(real.read_bytes()).hexdigest(), digest)
