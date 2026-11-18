"""Per-user time zones: "today" is the writer's day, even on a UTC server.

Time is frozen by patching timeutil's clock (helpers.freeze_time).
"""
from datetime import date, datetime
import importlib
from pathlib import Path
import re
import unittest

from accounts import adopt_browser_timezone
from database import connect
import timeutil
from tests.helpers import (SECURITY_OFF, TEST_EMAIL, TEST_PASSWORD, add_user, freeze_time,
                           log_in_as, new_test_database, skip_daily_maintenance)

# 02:00 UTC on Nov 18 is 21:00 on Nov 17 in New York and 07:45 on Nov 18 in Kathmandu.
UTC_INSTANT = datetime(2026, 11, 18, 2, 0)
NEW_YORK = 'America/New_York'
KATHMANDU = 'Asia/Kathmandu'


class TimeutilTests(unittest.TestCase):
    def test_the_same_instant_is_a_different_day(self):
        freeze_time(self, UTC_INSTANT)
        self.assertEqual(timeutil.now_in(NEW_YORK), datetime(2026, 11, 17, 21, 0))
        self.assertEqual(timeutil.now_in(KATHMANDU), datetime(2026, 11, 18, 7, 45))
        self.assertEqual(timeutil.utc_now(), UTC_INSTANT)
        self.assertEqual(timeutil.utc_today(), date(2026, 11, 18))
        # Naive, like every stored timestamp.
        self.assertIsNone(timeutil.now_in(NEW_YORK).tzinfo)
        self.assertIsNone(timeutil.utc_now().tzinfo)

    def test_invalid_or_missing_zones_are_utc(self):
        self.assertEqual(timeutil.normalize_timezone('US/Eastern'), NEW_YORK)
        self.assertNotIn('US/Eastern', timeutil.timezone_choices())
        self.assertIn('UTC', timeutil.timezone_choices())
        for name in (None, '', '  ', 'Mars/Olympus_Mons', 'america/new_york', 'Factory', 42):
            with self.subTest(name=name):
                self.assertEqual(timeutil.normalize_timezone(name), 'UTC')
                self.assertEqual(str(timeutil.zone(name)), 'UTC')
        self.assertEqual(timeutil.normalize_timezone(f' {NEW_YORK} '), NEW_YORK)
        self.assertIn(KATHMANDU, timeutil.timezone_choices())
        self.assertEqual(list(timeutil.timezone_choices()), sorted(timeutil.timezone_choices()))

    def test_logged_out_is_utc(self):
        freeze_time(self, UTC_INSTANT)
        self.assertEqual(timeutil.user_now(), UTC_INSTANT)   # no request at all
        app = importlib.import_module('app').app
        with app.test_request_context('/'):
            self.assertEqual(timeutil.user_today(), date(2026, 11, 18))


class ZoneAppTestCase(unittest.TestCase):
    zone = NEW_YORK

    def setUp(self):
        self.module = importlib.import_module('app')
        self.path = new_test_database(self)
        self.app = self.module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': self.path})
        skip_daily_maintenance(self.module, self.path, date(2026, 11, 18))
        skip_daily_maintenance(self.module, self.path, date(2026, 11, 22))
        self.user_id = add_user(self.path, timezone=self.zone)
        self.client = self.app.test_client()
        log_in_as(self.client, self.user_id)

    def add(self, created_at, content='Words'):
        db = connect(self.path)
        try:
            entry_id = db.insert('INSERT INTO entries (content, created_at, user_id) VALUES (?, ?, ?)',
                                 (content, created_at, self.user_id))
            db.commit()
            return entry_id
        finally:
            db.close()

    def stored(self, sql, *params):
        db = connect(self.path)
        try:
            return db.fetchone(sql, params)
        finally:
            db.close()

    def h1(self):
        return re.search(r'<h1>(.*?)</h1>', self.client.get('/').text).group(1)


class NewYorkTests(ZoneAppTestCase):
    zone = NEW_YORK

    def test_today_greeting_and_new_entries_use_the_users_day(self):
        freeze_time(self, UTC_INSTANT)
        self.assertTrue(self.h1().startswith('Good evening'))
        home = self.client.get('/').text
        self.assertIn('Tuesday, November 17, 2026', home)
        response = self.client.post('/write', data={'journal': 'Late night thoughts'})
        entry_id = int(response.location.split('/entry/')[1].split('?')[0])
        created_at = self.stored('SELECT created_at FROM entries WHERE id = ?', entry_id)[0]
        self.assertTrue(created_at.startswith('2026-11-17T21:00'), created_at)
        self.assertIn('You wrote today', self.client.get('/').text)

    def test_trash_and_exports_use_the_users_day(self):
        freeze_time(self, UTC_INSTANT)
        entry_id = self.add('2026-11-10T09:00:00')
        self.client.post(f'/delete/{entry_id}')
        deleted_at = self.stored('SELECT deleted_at FROM entries WHERE id = ?', entry_id)[0]
        self.assertTrue(deleted_at.startswith('2026-11-17T21:00'), deleted_at)
        self.client.post(f'/restore/{entry_id}')
        response = self.client.get('/backup/export.json')
        self.assertIn('my-journal-2026-11-17.json', response.headers['Content-Disposition'])
        self.assertTrue(response.get_json()['exported_at'].startswith('2026-11-17T21:00'))

    def test_history_marks_the_users_today(self):
        freeze_time(self, UTC_INSTANT)
        page = self.client.get('/history').text
        today_cell = re.search(r'class="calendar-day[^"]*is-today[^"]*"\s+aria-label="([^"]+)"', page)
        self.assertEqual(today_cell.group(1), 'November 17, 2026')

    def test_streak_counts_the_users_days(self):
        # Written on the 14th, 15th and 16th. In New York it is still the
        # 17th, so the streak is alive (3 days, "write today to keep it").
        for day in (14, 15, 16):
            self.add(f'2026-11-{day}T09:00:00')
        freeze_time(self, UTC_INSTANT)
        self.assertIn('Write today to keep your 3-day streak', self.client.get('/').text)
        self.assertIn('>3<', self.client.get('/insights').text.replace(' ', ''))

    def test_week_view_uses_the_users_week(self):
        # Sunday 03:00 UTC is still Saturday evening in New York.
        freeze_time(self, datetime(2026, 11, 22, 3, 0))
        page = self.client.get('/week').text
        self.assertIn('Week of Nov 15 – 21, 2026', page)
        # No arrow into the future: the next-week arrow is disabled.
        self.assertIn('aria-disabled="true" aria-label="Next week"', page)
        self.assertNotIn('start=2026-11-22', page)
        # The user's next week hasn't started, so asking for it shows this week.
        self.assertIn('Week of Nov 15 – 21, 2026', self.client.get('/week?start=2026-11-22').text)


class KathmanduTests(ZoneAppTestCase):
    zone = KATHMANDU

    def test_the_same_instant_is_already_tomorrow(self):
        freeze_time(self, UTC_INSTANT)
        self.assertTrue(self.h1().startswith('Good morning'))
        self.assertIn('Wednesday, November 18, 2026', self.client.get('/').text)
        response = self.client.post('/write', data={'journal': 'Morning pages'})
        entry_id = int(response.location.split('/entry/')[1].split('?')[0])
        created_at = self.stored('SELECT created_at FROM entries WHERE id = ?', entry_id)[0]
        self.assertTrue(created_at.startswith('2026-11-18T07:45'), created_at)

    def test_streak_is_broken_on_the_users_day(self):
        # Same entries as the New York test: here it is already the 18th and
        # nothing was written on the 17th, so the streak is over.
        for day in (14, 15, 16):
            self.add(f'2026-11-{day}T09:00:00')
        freeze_time(self, UTC_INSTANT)
        home = self.client.get('/').text
        self.assertNotIn('-day streak', home)

    def test_week_view_has_moved_on(self):
        freeze_time(self, datetime(2026, 11, 22, 3, 0))
        self.assertIn('Week of Nov 22 – 28, 2026', self.client.get('/week').text)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('app')
        self.path = new_test_database(self)
        self.app = self.module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': self.path})
        skip_daily_maintenance(self.module, self.path)

    def zone_of(self, email):
        db = connect(self.path)
        try:
            return db.fetchone('SELECT timezone FROM users WHERE email = ?', (email,))[0]
        finally:
            db.close()

    def set_zone(self, email, zone):
        db = connect(self.path)
        with db:
            db.execute('UPDATE users SET timezone = ? WHERE email = ?', (zone, email))
        db.close()

    def test_forms_carry_the_browser_zone(self):
        for url in ('/signup', '/login'):
            with self.subTest(url=url):
                page = self.app.test_client().get(url).text
                self.assertIn('<input type="hidden" name="timezone" value="" data-browser-timezone>', page)
                self.assertIn('Intl.DateTimeFormat().resolvedOptions().timeZone', page)

    def test_signup_stores_the_browser_zone_and_falls_back_to_utc(self):
        for number, (sent, stored) in enumerate(((KATHMANDU, KATHMANDU), ('Not/AZone', 'UTC'),
                                                 ('', 'UTC'), (None, 'UTC'))):
            with self.subTest(sent=sent):
                email = f'user{number}@example.com'
                data = {'email': email, 'password': 'a-good-password'}
                if sent is not None:
                    data['timezone'] = sent
                response = self.app.test_client().post('/signup', data=data)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.zone_of(email), stored)

    def test_old_names_from_the_browser_are_stored_as_current_names(self):
        self.app.test_client().post('/signup', data={'email': 'india@example.com',
                                                      'password': 'a-good-password',
                                                      'timezone': 'Asia/Calcutta'})
        self.assertEqual(self.zone_of('india@example.com'), 'Asia/Kolkata')
        add_user(self.path, timezone='UTC')
        self.login('Asia/Katmandu')
        self.assertEqual(self.zone_of(TEST_EMAIL), KATHMANDU)

    def test_signup_uses_the_zone_straight_away(self):
        freeze_time(self, UTC_INSTANT)
        client = self.app.test_client()
        client.post('/signup', data={'email': 'new@example.com', 'password': 'a-good-password',
                                     'timezone': NEW_YORK})
        self.assertIn('Tuesday, November 17, 2026', client.get('/').text)

    def login(self, zone):
        return self.app.test_client().post('/login', data={
            'email': TEST_EMAIL, 'password': TEST_PASSWORD, 'timezone': zone})

    def test_login_fills_in_a_missing_or_utc_zone(self):
        add_user(self.path)   # no zone
        self.assertEqual(self.login(KATHMANDU).status_code, 302)
        self.assertEqual(self.zone_of(TEST_EMAIL), KATHMANDU)
        self.set_zone(TEST_EMAIL, 'UTC')
        self.login(NEW_YORK)
        self.assertEqual(self.zone_of(TEST_EMAIL), NEW_YORK)

    def test_login_never_overwrites_a_chosen_zone(self):
        add_user(self.path, timezone='Europe/Paris')
        self.assertEqual(self.login(KATHMANDU).status_code, 302)   # traveling
        self.assertEqual(self.zone_of(TEST_EMAIL), 'Europe/Paris')

    def test_login_ignores_an_invalid_zone(self):
        add_user(self.path, timezone='UTC')
        self.login('Not/AZone')
        self.assertEqual(self.zone_of(TEST_EMAIL), 'UTC')
        self.login('')
        self.assertEqual(self.zone_of(TEST_EMAIL), 'UTC')

    def test_adopt_rule(self):
        db = connect(self.path)
        user_id = add_user(self.path)
        try:
            self.assertEqual(adopt_browser_timezone(db, user_id, 'Europe/Paris', KATHMANDU), 'Europe/Paris')
            self.assertEqual(adopt_browser_timezone(db, user_id, None, 'nonsense'), 'UTC')
            self.assertEqual(adopt_browser_timezone(db, user_id, 'UTC', KATHMANDU), KATHMANDU)
        finally:
            db.close()


class SettingsTimezoneTests(ZoneAppTestCase):
    zone = NEW_YORK

    def test_row_shows_the_zone_and_the_menu_selects_it(self):
        page = self.client.get('/settings').text
        self.assertRegex(page, r'<span class="settings-label">Time zone<span class="visually-hidden">:</span>'
                               r'</span>\s*<span class="settings-value">America/New_York</span>')
        self.assertRegex(page, r'<option value="America/New_York" data-aliases="[^"]*US/Eastern[^"]*" '
                               r'selected>America/New_York</option>')
        self.assertEqual(page.count(' selected>'), 1)
        self.assertIn("Use this device's time zone", page)
        options = re.findall(r'<option value="([^"]+)"', page)
        self.assertEqual(options, sorted(options))
        self.assertEqual(len(options), len(timeutil.timezone_choices()))

    def test_old_names_are_saved_as_current_names(self):
        # Chrome reports Asia/Calcutta and Asia/Katmandu.
        for sent, saved in (('Asia/Calcutta', 'Asia/Kolkata'), ('Asia/Katmandu', KATHMANDU),
                            ('Etc/UTC', 'UTC')):
            with self.subTest(sent=sent):
                self.assertEqual(self.client.post('/settings/timezone', data={'timezone': sent}).status_code, 302)
                self.assertEqual(self.stored('SELECT timezone FROM users WHERE id = ?', self.user_id)[0], saved)
        page = self.client.get('/settings').text
        self.assertRegex(page, r'<option value="Asia/Kolkata" data-aliases="[^"]*Asia/Calcutta')
        self.assertNotIn('<option value="Asia/Calcutta"', page)

    def test_saving_a_valid_zone(self):
        response = self.client.post('/settings/timezone', data={'timezone': KATHMANDU},
                                    follow_redirects=True)
        self.assertIn('Time zone saved.', response.text)
        self.assertEqual(self.stored('SELECT timezone FROM users WHERE id = ?', self.user_id)[0],
                         KATHMANDU)
        freeze_time(self, UTC_INSTANT)
        self.assertTrue(self.h1().startswith('Good morning'))   # takes effect at once

    def test_an_invalid_zone_is_refused_with_the_row_open(self):
        for bad in ('Mars/Olympus_Mons', '', 'UTC; DROP TABLE users'):
            with self.subTest(bad=bad):
                response = self.client.post('/settings/timezone', data={'timezone': bad})
                self.assertEqual(response.status_code, 400)
                self.assertIn('Choose a time zone from the list', response.text)
                self.assertRegex(response.text, r'id="settings-timezone"\s+open')
        self.assertEqual(self.stored('SELECT timezone FROM users WHERE id = ?', self.user_id)[0],
                         NEW_YORK)


class SystemTimeTests(ZoneAppTestCase):
    zone = KATHMANDU

    def test_new_accounts_record_created_at_in_utc(self):
        freeze_time(self, UTC_INSTANT)
        client = self.app.test_client()
        client.post('/signup', data={'email': 'utc@example.com', 'password': 'a-good-password',
                                     'timezone': KATHMANDU})
        created_at, changed_at = self.stored(
            'SELECT created_at, password_changed_at FROM users WHERE email = ?', 'utc@example.com')
        self.assertTrue(created_at.startswith('2026-11-18T02:00'), created_at)       # system: UTC
        self.assertTrue(changed_at.startswith('2026-11-18T07:45'), changed_at)       # shown: local

    def test_only_timeutil_reads_the_real_clock(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob('*.py'):
            relative = path.relative_to(root)
            if relative.parts[0] in ('tests', '.venv', 'venv') or relative.name == 'timeutil.py':
                continue
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if re.search(r'date\.today\(\)|datetime\.now\(|datetime\.utcnow\(', line):
                    offenders.append(f'{relative}:{number}: {line.strip()}')
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
