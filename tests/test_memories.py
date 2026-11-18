""""On this day" memories, using temporary databases only."""
from datetime import date, timedelta
import hashlib
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from database import connect, initialize_database
from memories import get_memories, subtract_months
from tags import set_entry_tags
from tests.helpers import SECURITY_OFF, add_user, new_test_database, signed_in_client

TODAY = date(2026, 11, 16)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.path = new_test_database(self)
        self.user_id = add_user(self.path)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()

    def add(self, day, time='09:00:00', title=None, content='Words', tags=()):
        cursor = self.db.insert(
            'INSERT INTO entries (title, content, mood, created_at, user_id) VALUES (?, ?, ?, ?, ?)',
            (title, content, 'Happy', f'{day.isoformat()}T{time}', self.user_id))
        if tags:
            set_entry_tags(self.db, cursor, tags, user_id=self.user_id)
        self.db.commit()
        return cursor

    def memories(self, today=TODAY, limit=10):
        return [(m['label'], m['entry']['id']) for m in get_memories(self.db, today, limit, user_id=self.user_id)]

    # ---------- date math ----------

    def test_subtract_months_clamps_the_day(self):
        self.assertEqual(subtract_months(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(subtract_months(date(2024, 3, 31), 1), date(2024, 2, 29))
        self.assertEqual(subtract_months(date(2026, 8, 31), 6), date(2026, 2, 28))
        self.assertEqual(subtract_months(date(2024, 2, 29), 12), date(2023, 2, 28))
        self.assertEqual(subtract_months(date(2026, 1, 15), 1), date(2025, 12, 15))

    # ---------- exact matches ----------

    def test_exact_matches_oldest_first(self):
        week = self.add(date(2026, 11, 9))
        month = self.add(date(2026, 10, 16))
        six = self.add(date(2026, 5, 16))
        year = self.add(date(2025, 11, 16))
        two = self.add(date(2024, 11, 16))
        self.add(date(2026, 11, 1))   # not on any target date
        self.assertEqual(self.memories(), [
            ('2 years ago', two), ('1 year ago', year), ('6 months ago', six),
            ('1 month ago', month), ('1 week ago', week),
        ])

    def test_every_earlier_year_with_entries(self):
        five = self.add(date(2021, 11, 16))
        three = self.add(date(2023, 11, 16))
        self.assertEqual(self.memories(), [('5 years ago', five), ('3 years ago', three)])

    def test_latest_entry_on_the_day_and_its_tags(self):
        self.add(date(2025, 11, 16), '08:00:00')
        latest = self.add(date(2025, 11, 16), '22:00:00', title='Evening', tags=['family', 'walks'])
        [memory] = get_memories(self.db, TODAY, user_id=self.user_id)
        self.assertEqual(memory['entry']['id'], latest)
        self.assertEqual(memory['entry']['title'], 'Evening')
        self.assertEqual(memory['entry']['tags'], ['family', 'walks'])

    # ---------- ±1 day fallback ----------

    def test_day_before_or_after_is_about(self):
        before = self.add(date(2026, 10, 15))   # 1 month ago is Oct 16
        after = self.add(date(2025, 11, 17))    # 1 year ago is Nov 16
        six = self.add(date(2026, 5, 17))
        week = self.add(date(2026, 11, 8))
        self.assertEqual(self.memories(), [
            ('About a year ago', after), ('About 6 months ago', six),
            ('About a month ago', before), ('About a week ago', week),
        ])

    def test_exact_day_wins_over_neighbours_and_two_days_off_is_ignored(self):
        self.add(date(2025, 11, 15))
        exact = self.add(date(2025, 11, 16))
        self.add(date(2026, 10, 14))            # two days before 1 month ago
        self.assertEqual(self.memories(), [('1 year ago', exact)])

    def test_years_fallback_reaches_the_oldest_entry(self):
        after = self.add(date(2023, 11, 17))    # day after "3 years ago"
        self.assertEqual(self.memories(), [('About 3 years ago', after)])

    # ---------- month ends and leap days ----------

    def test_march_31(self):
        today = date(2026, 3, 31)
        feb = self.add(date(2026, 2, 28))
        self.assertEqual(self.memories(today), [('1 month ago', feb)])

        leap_today = date(2028, 3, 31)
        leap_feb = self.add(date(2028, 2, 29))
        self.assertEqual(self.memories(leap_today), [('1 month ago', leap_feb)])

    def test_february_29(self):
        today = date(2028, 2, 29)
        last_year = self.add(date(2027, 2, 28))
        four_years = self.add(date(2024, 2, 28))   # Feb 29 2024 existed, so this is "About"
        self.assertEqual(self.memories(today), [
            ('About 4 years ago', four_years), ('1 year ago', last_year),
        ])

    # ---------- exclusions, limit, order ----------

    def test_today_and_future_are_never_shown(self):
        self.add(TODAY)
        self.add(TODAY + timedelta(days=1))
        self.assertEqual(self.memories(), [])

    def test_an_entry_is_never_shown_twice(self):
        # Real targets are never within two days of each other, so add a
        # second target on the same date to force a shared candidate.
        with mock.patch('memories._MONTH_STEPS', [('1 month ago', 1), ('also 1 month ago', 1)]):
            only = self.add(date(2026, 10, 16))
            self.assertEqual([entry_id for _, entry_id in self.memories()], [only])

    def test_limit_keeps_the_oldest(self):
        ids = [self.add(date(2026 - years, 11, 16)) for years in range(1, 6)]
        self.add(date(2026, 11, 9))
        result = get_memories(self.db, TODAY, user_id=self.user_id)                # default limit is 3
        self.assertEqual([m['entry']['id'] for m in result], [ids[4], ids[3], ids[2]])
        self.assertEqual([m['label'] for m in result], ['5 years ago', '4 years ago', '3 years ago'])
        self.assertEqual(len(get_memories(self.db, TODAY, limit=1, user_id=self.user_id)), 1)
        self.assertEqual(get_memories(self.db, TODAY, limit=0, user_id=self.user_id), [])

    def test_empty_database(self):
        self.assertEqual(get_memories(self.db, TODAY, user_id=self.user_id), [])


class HomeMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = Path('journal.db')
        cls.digest = hashlib.sha256(cls.real.read_bytes()).hexdigest() if cls.real.exists() else None
        cls.module = importlib.import_module('app')
        cls.app = cls.module.app
        cls.old_config = cls.app.config['DATABASE']

    @classmethod
    def tearDownClass(cls):
        cls.app.config['DATABASE'] = cls.old_config
        if cls.digest:
            assert hashlib.sha256(cls.real.read_bytes()).hexdigest() == cls.digest

    def setUp(self):
        self.path = new_test_database(self)
        self.app.config.update(TESTING=True, **SECURITY_OFF, DATABASE=self.path)
        self.client, self.user_id = signed_in_client(self.app, self.path)

    def add(self, created_at, title, tags=()):
        db = connect(self.path)
        try:
            entry_id = db.insert(
                "INSERT INTO entries (title, content, mood, created_at, user_id) "
                "VALUES (?, 'A long walk', 'Sad', ?, ?)", (title, created_at, self.user_id))
            set_entry_tags(db, entry_id, tags, user_id=self.user_id)
            db.commit()
            return entry_id
        finally:
            db.close()

    def test_home_shows_memories_below_the_today_card(self):
        today = date.today()
        last_year = subtract_months(today, 12)
        entry_id = self.add(f'{last_year.isoformat()}T10:00:00', 'Then', ['walks'])
        self.add(f'{last_year.isoformat()}T08:00:00', None)   # earlier that day: not shown
        page = self.client.get('/').text
        self.assertIn('On this day', page)
        self.assertLess(page.index('today-card'), page.index('On this day'))
        section = page[page.index('On this day'):page.index('Your last two weeks')]
        label = f"1 year ago · {last_year.strftime('%b %d, %Y').replace(' 0', ' ')}"
        self.assertIn(label, section)
        self.assertIn(f'href="/entry/{entry_id}"', section)
        self.assertIn('class="entry-row mood-sad', section)
        self.assertIn('>Then</h3>', section)
        self.assertIn('mood-badge mood-sad', section)
        self.assertIn('<span class="tag-pill"><span aria-hidden="true">#</span>walks</span>', section)
        self.assertIn('entry-row-preview">A long walk', section)

    def test_untitled_memory_uses_the_date(self):
        week = date.today() - timedelta(weeks=1)
        self.add(f'{week.isoformat()}T10:00:00', '')
        page = self.client.get('/').text
        friendly = week.strftime('%B %d, %Y').replace(' 0', ' ')
        self.assertIn(f'<h3 class="entry-row-title">{friendly}</h3>', page)

    def test_home_hides_the_section_without_memories(self):
        self.assertNotIn('On this day', self.client.get('/').text)   # empty journal
        self.add(f'{date.today().isoformat()}T07:00:00', 'Today only')
        self.add(f'{(date.today() - timedelta(days=3)).isoformat()}T07:00:00', 'Recent')
        page = self.client.get('/').text
        self.assertIn('Today only', page)        # recent entries still listed
        self.assertNotIn('On this day', page)


if __name__ == '__main__':
    unittest.main()
