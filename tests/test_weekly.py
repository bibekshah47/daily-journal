"""Weekly review, using temporary databases only."""
from datetime import date, timedelta
import hashlib
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from database import connect, initialize_database
from tags import set_entry_tags
from tests.helpers import SECURITY_OFF, add_user, signed_in_client
from weekly import get_week_review, week_bounds, week_label

SUNDAY = date(2026, 11, 15)   # the week of Nov 15 – 21, 2026


class WeekBoundsTests(unittest.TestCase):
    def test_sunday_wednesday_and_saturday(self):
        for day in (date(2026, 11, 15), date(2026, 11, 18), date(2026, 11, 21)):
            with self.subTest(day=day):
                self.assertEqual(week_bounds(day), (date(2026, 11, 15), date(2026, 11, 21)))

    def test_week_crossing_a_month(self):
        expected = (date(2026, 11, 29), date(2026, 12, 5))
        self.assertEqual(week_bounds(date(2026, 11, 29)), expected)   # Sunday
        self.assertEqual(week_bounds(date(2026, 12, 2)), expected)    # Wednesday
        self.assertEqual(week_bounds(date(2026, 12, 5)), expected)    # Saturday

    def test_week_crossing_a_year(self):
        expected = (date(2026, 12, 27), date(2027, 1, 2))
        self.assertEqual(week_bounds(date(2026, 12, 27)), expected)   # Sunday
        self.assertEqual(week_bounds(date(2026, 12, 30)), expected)   # Wednesday
        self.assertEqual(week_bounds(date(2027, 1, 2)), expected)     # Saturday

    def test_labels(self):
        self.assertEqual(week_label(date(2026, 11, 15)), 'Nov 15 – 21, 2026')
        self.assertEqual(week_label(date(2026, 11, 29)), 'Nov 29 – Dec 5, 2026')
        self.assertEqual(week_label(date(2026, 12, 27)), 'Dec 27, 2026 – Jan 2, 2027')
        self.assertEqual(week_label(date(2026, 11, 1)), 'Nov 1 – 7, 2026')


class WeekReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'journal.db')
        initialize_database(self.path)
        self.user_id = add_user(self.path)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, created_at, content='Words', mood=None, title=None, tags=(),
            went_well=None, went_wrong=None, improve_tomorrow=None, deleted_at=None):
        cursor = self.db.execute(
            'INSERT INTO entries (title, content, mood, created_at, went_well, went_wrong, '
            'improve_tomorrow, deleted_at, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (title, content, mood, created_at, went_well, went_wrong, improve_tomorrow, deleted_at,
             self.user_id))
        if tags:
            set_entry_tags(self.db, cursor.lastrowid, tags, user_id=self.user_id)
        self.db.commit()
        return cursor.lastrowid

    def review(self, start=SUNDAY):
        return get_week_review(self.db, start, user_id=self.user_id)

    def test_empty_week(self):
        review = self.review()
        self.assertEqual([day['date'] for day in review['days']],
                         [SUNDAY + timedelta(days=n) for n in range(7)])
        self.assertFalse(any(day['written'] for day in review['days']))
        self.assertEqual((review['days_written'], review['entry_count'], review['word_count']), (0, 0, 0))
        self.assertEqual(review['reflections'], {'went_well': [], 'went_wrong': [], 'improve_tomorrow': []})
        self.assertEqual((review['tags'], review['entries']), ([], []))

    def test_stats_days_and_latest_mood(self):
        self.add('2026-11-14T23:59:00', 'Saturday before is outside')
        self.add('2026-11-15T00:00:00', 'one two three', mood='Sad')
        self.add('2026-11-17T08:00:00', 'Morning words here', mood='Stressed')
        self.add('2026-11-17T21:00:00', '  four\nfive\tsix  seven ', mood='Happy')
        self.add('2026-11-21T23:59:59', 'last', mood=None)
        self.add('2026-11-22T00:00:00', 'next Sunday is outside')
        review = self.review()

        self.assertEqual(review['days_written'], 3)
        self.assertEqual(review['entry_count'], 4)
        self.assertEqual(review['word_count'], 3 + 3 + 4 + 1)
        by_day = {day['date'].day: (day['written'], day['mood']) for day in review['days']}
        self.assertEqual(by_day, {15: (True, 'Sad'), 16: (False, ''), 17: (True, 'Happy'),
                                  18: (False, ''), 19: (False, ''), 20: (False, ''), 21: (True, '')})

    def test_any_day_of_the_week_gives_the_same_review(self):
        self.add('2026-11-18T09:00:00', 'Midweek')
        self.assertEqual(self.review(date(2026, 11, 18)), self.review(SUNDAY))
        self.assertEqual(self.review(date(2026, 11, 18))['start'], SUNDAY)

    def test_word_count_uses_plain_text_content(self):
        # content is the plain text; content_html must not be counted.
        self.db.execute(
            "INSERT INTO entries (content, content_html, created_at, user_id) VALUES (?, ?, ?, ?)",
            ('Bold and plain', '<p><strong>Bold</strong> and plain</p>', '2026-11-16T10:00:00', self.user_id))
        self.db.commit()
        self.assertEqual(self.review()['word_count'], 3)

    def test_reflections_grouped_in_date_order_skipping_blanks(self):
        self.add('2026-11-19T09:00:00', went_well='Thursday win', went_wrong='   ',
                 improve_tomorrow='')
        self.add('2026-11-16T09:00:00', went_well='  Monday win  ', went_wrong='Monday miss')
        self.add('2026-11-16T20:00:00', went_well='Monday evening win', improve_tomorrow='\n\t')
        self.add('2026-11-20T09:00:00')   # no reflection at all
        reflections = self.review()['reflections']
        self.assertEqual(reflections['went_well'], [
            (date(2026, 11, 16), 'Monday win'),
            (date(2026, 11, 16), 'Monday evening win'),
            (date(2026, 11, 19), 'Thursday win'),
        ])
        self.assertEqual(reflections['went_wrong'], [(date(2026, 11, 16), 'Monday miss')])
        self.assertEqual(reflections['improve_tomorrow'], [])

    def test_tags_with_counts_and_entries_newest_first(self):
        first = self.add('2026-11-15T09:00:00', tags=['work', 'deep-work'])
        second = self.add('2026-11-18T09:00:00', tags=['family', 'work'])
        third = self.add('2026-11-18T09:00:00', tags=['work'])   # same time: higher id is newer
        self.add('2026-11-08T09:00:00', tags=['old-week'])
        review = self.review()
        self.assertEqual(review['tags'], [
            {'name': 'work', 'count': 3},
            {'name': 'deep-work', 'count': 1},
            {'name': 'family', 'count': 1},
        ])
        self.assertEqual([entry['id'] for entry in review['entries']], [third, second, first])
        self.assertEqual(review['entries'][1]['tags'], ['family', 'work'])

    def test_trashed_entries_are_excluded(self):
        self.add('2026-11-16T09:00:00', 'kept words', mood='Happy', tags=['kept'])
        self.add('2026-11-16T22:00:00', 'trashed later entry', mood='Sad', tags=['gone'],
                 went_well='Hidden', deleted_at='2026-11-17T10:00:00')
        self.add('2026-11-18T09:00:00', 'trashed', tags=['gone'],
                 went_wrong='Hidden too', deleted_at='2026-11-18T10:00:00')
        review = self.review()
        self.assertEqual(review['entry_count'], 1)
        self.assertEqual(review['days_written'], 1)
        self.assertEqual(review['word_count'], 2)
        self.assertEqual(review['days'][1]['mood'], 'Happy')
        self.assertFalse(review['days'][3]['written'])
        self.assertEqual(review['tags'], [{'name': 'kept', 'count': 1}])
        self.assertEqual(review['reflections'], {'went_well': [], 'went_wrong': [], 'improve_tomorrow': []})


class _FixedDate(date):
    @classmethod
    def today(cls):
        return date(2026, 11, 17)   # a Tuesday


class WeekPageTests(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'journal.db')
        initialize_database(self.path)
        self.app.config.update(TESTING=True, **SECURITY_OFF, DATABASE=self.path)
        self.client, self.user_id = signed_in_client(self.app, self.path)
        # Pin "today" (in the app's daily hook and the /week route), and skip
        # the once-a-day backup/cleanup hook.
        for module in (self.module, importlib.import_module('views.insights')):
            patcher = mock.patch.object(module, 'date', _FixedDate)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.module._daily_backups_checked.add((os.path.abspath(self.path), _FixedDate.today()))

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, created_at, content='Words', **fields):
        db = connect(self.path)
        try:
            columns = ['content', 'created_at', 'user_id', *fields]
            cursor = db.execute(
                f"INSERT INTO entries ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                (content, created_at, self.user_id, *fields.values()))
            db.commit()
            return cursor.lastrowid
        finally:
            db.close()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_current_week(self):
        self.add('2026-11-16T09:00:00', 'Hello there world', title='Monday notes', mood='Happy',
                 went_well='Shipped it', went_wrong='  ')
        page = self.get('/week')
        self.assertIn('Week of Nov 15 – 21, 2026', page)
        self.assertIn('Monday notes', page)
        self.assertIn('What went well today?', page)
        self.assertIn('Shipped it', page)
        self.assertIn('>Mon</span>', page)
        self.assertNotIn("What didn&#39;t go well today?", page)
        self.assertNotIn('No reflections this week.', page)
        self.assertIn('href="/week?start=2026-11-08"', page)
        self.assertNotIn('>This week</a>', page)

    def test_next_arrow_disabled_on_current_week(self):
        page = self.get('/week')
        self.assertIn('aria-disabled="true" aria-label="Next week"', page)
        self.assertNotIn('/week?start=2026-11-22', page)

    def test_wednesday_start_snaps_to_sunday(self):
        self.add('2026-11-04T09:00:00', 'Earlier week entry')
        page = self.get('/week?start=2026-11-04')
        self.assertIn('Week of Nov 1 – 7, 2026', page)
        self.assertIn('Earlier week entry', page)
        self.assertIn('href="/week?start=2026-10-25"', page)
        self.assertIn('href="/week?start=2026-11-08" class="icon-button" aria-label="Next week"', page)
        self.assertNotIn('aria-disabled="true"', page)
        self.assertIn('>This week</a>', page)
        self.assertIn('No reflections this week.', page)

    def test_previous_week_links_forward_to_current_week(self):
        page = self.get('/week?start=2026-11-10')
        self.assertIn('href="/week?start=2026-11-15" class="icon-button" aria-label="Next week"', page)

    def test_invalid_and_future_start_show_current_week(self):
        for start in ('nonsense', '2026-13-01', '', '2027-01-01', '0001-01-01', '0001-01-10'):
            with self.subTest(start=start):
                page = self.get(f'/week?start={start}')
                self.assertIn('Week of Nov 15 – 21, 2026', page)
                self.assertIn('aria-disabled="true"', page)

    def test_year_one_week_has_no_overflow(self):
        page = self.get('/week?start=0001-01-17')
        self.assertIn('Week of Jan 14 – 20, 1', page)

    def test_empty_week(self):
        page = self.get('/week?start=2026-11-01')
        self.assertIn('Nothing written this week', page)
        self.assertIn('Write an entry', page)
        self.assertIn('href="/write"', page)
        self.assertNotIn('Reflections', page)

    def test_tags_link_to_history_and_trash_is_hidden(self):
        db = connect(self.path)
        try:
            for created_at, tags, deleted in (('2026-11-16T09:00:00', ['work'], None),
                                              ('2026-11-17T09:00:00', ['work', 'trip'], None),
                                              ('2026-11-17T10:00:00', ['secret'], '2026-11-17T11:00:00')):
                cursor = db.execute(
                    'INSERT INTO entries (content, created_at, title, deleted_at, user_id) '
                    'VALUES (?, ?, ?, ?, ?)', ('Words', created_at, f'Entry {tags[0]}', deleted, self.user_id))
                set_entry_tags(db, cursor.lastrowid, tags, user_id=self.user_id)
            db.commit()
        finally:
            db.close()
        page = self.get('/week')
        self.assertIn('href="/history?tag=work"', page)
        self.assertIn('href="/history?tag=trip"', page)
        self.assertIn('· 2', page)
        self.assertNotIn('secret', page)

    def test_insights_links_to_week(self):
        page = self.get('/insights')
        self.assertIn('href="/week"', page)
        self.assertIn('Review this week', page)


if __name__ == '__main__':
    unittest.main()
