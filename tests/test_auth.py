"""Accounts and data isolation, using temporary databases only."""
from datetime import date, timedelta
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from database import connect, initialize_database
from memories import subtract_months
from tags import set_entry_tags
from tests.helpers import TEST_PASSWORD, add_user, log_in_as, signed_in_client


def login_target(location):
    """'/login?next=...' -> the decoded next value (None if there is none)."""
    parts = urlsplit(location)
    assert parts.path == '/login', location
    return parse_qs(parts.query).get('next', [None])[0]


class AuthTestCase(unittest.TestCase):
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
        self.app.config.update(TESTING=True, DATABASE=self.path)
        self.client = self.app.test_client()
        # Skip the once-a-day backup/cleanup hook.
        self.module._daily_backups_checked.add((os.path.abspath(self.path), date.today()))

    def tearDown(self):
        self.tmp.cleanup()

    def query(self, sql, *params):
        db = connect(self.path)
        try:
            return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def signup(self, email, password='a-good-password', client=None):
        return (client or self.client).post('/signup', data={'email': email, 'password': password})

    def login(self, email, password='a-good-password', client=None, **extra):
        return (client or self.client).post('/login', data={'email': email, 'password': password, **extra})


class SignupTests(AuthTestCase):
    def test_signup_logs_in_and_welcomes(self):
        response = self.signup('  New.Person@Example.COM ', 'long enough pw')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/')
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertIn('Welcome! Your journal is ready.', home.text)
        [(email, password_hash)] = self.query('SELECT email, password_hash FROM users')
        self.assertEqual(email, 'new.person@example.com')        # trimmed and lowercase
        self.assertNotIn('long enough pw', password_hash)        # never stored in plain text
        self.assertTrue(password_hash.startswith(('scrypt:', 'pbkdf2:')))

    def test_duplicate_email_any_case(self):
        self.signup('ann@example.com')
        other = self.app.test_client()
        response = self.signup('ANN@example.com', 'another-password', client=other)
        self.assertEqual(response.status_code, 400)
        self.assertIn('An account with this email already exists.', response.text)
        self.assertIn('<a href="/login">Log in instead?</a>', response.text)
        self.assertEqual(self.query('SELECT COUNT(*) FROM users')[0][0], 1)
        self.assertEqual(other.get('/').status_code, 302)        # not logged in

    def test_invalid_email(self):
        for email in ['', 'no-at-sign', 'a@b', 'a b@example.com', '@example.com', 'x' * 250 + '@e.com']:
            with self.subTest(email=email):
                response = self.signup(email)
                self.assertEqual(response.status_code, 400)
                self.assertIn('Enter a valid email', response.text)
                self.assertIn('aria-describedby="email-error"', response.text)
        self.assertEqual(self.query('SELECT COUNT(*) FROM users')[0][0], 0)

    def test_short_and_too_long_password(self):
        response = self.signup('ann@example.com', 'seven77')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Use at least 8 characters', response.text)
        self.assertNotIn('seven77', response.text)               # never echoed back
        self.assertIn('value="ann@example.com"', response.text)  # email is kept
        response = self.signup('ann@example.com', 'x' * 129)
        self.assertIn('Use 128 characters or fewer', response.text)
        self.assertEqual(self.query('SELECT COUNT(*) FROM users')[0][0], 0)
        self.assertEqual(self.signup('ann@example.com', 'x' * 128).status_code, 302)

    def test_signup_page(self):
        page = self.client.get('/signup').text
        self.assertIn('autocomplete="email"', page)
        self.assertIn('autocomplete="new-password"', page)
        self.assertIn('Show password', page)
        self.assertIn('Already have an account? <a href="/login">Log in</a>', page)
        self.assertNotIn('class="sidebar"', page)               # no sidebar or tab bar
        self.assertIn('no-sidebar', page)


class LoginTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user_id = add_user(self.path, 'ann@example.com', TEST_PASSWORD)

    def test_login_page(self):
        page = self.client.get('/login').text
        self.assertIn('autocomplete="email"', page)
        self.assertIn('autocomplete="current-password"', page)
        self.assertIn('Keep me logged in', page)
        self.assertIn('New here? <a href="/signup">Create an account</a>', page)
        self.assertNotIn('class="sidebar"', page)

    def test_login_and_logout(self):
        response = self.login(' ANN@example.com', TEST_PASSWORD)
        self.assertEqual((response.status_code, response.location), (302, '/'))
        self.assertNotIn('remember_token', response.headers.get('Set-Cookie', ''))
        home = self.client.get('/').text
        self.assertIn('class="sidebar-email" title="ann@example.com">ann@example.com</span>', home)
        self.assertIn('<form method="POST" action="/logout">', home)
        self.assertIn('class="mobile-logout"', home)              # phones: Log out in Home's header

        self.assertEqual(self.client.get('/logout').status_code, 405)   # a form button, not a link
        response = self.client.post('/logout')
        self.assertEqual((response.status_code, response.location), (302, '/login'))
        self.assertIn('Logged out.', self.client.get('/login').text)
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_keep_me_logged_in_sets_a_remember_cookie(self):
        response = self.login('ann@example.com', TEST_PASSWORD, remember='1')
        cookie = response.headers.get('Set-Cookie', '')
        self.assertIn('remember_token=', ' '.join(response.headers.getlist('Set-Cookie')))
        self.assertTrue(any('HttpOnly' in c and 'SameSite=Lax' in c
                            for c in response.headers.getlist('Set-Cookie') if 'remember_token' in c), cookie)

    def test_wrong_password_and_unknown_email_get_the_same_message(self):
        for email, password in [('ann@example.com', 'wrong password'),
                                ('nobody@example.com', TEST_PASSWORD),
                                ('ann@example.com', ''),
                                ('ann@example.com', 'x' * 10000)]:
            with self.subTest(email=email, password=password[:20]):
                response = self.login(email, password)
                self.assertEqual(response.status_code, 400)
                self.assertIn('Email or password is incorrect.', response.text)
                self.assertNotIn('wrong password', response.text)
                self.assertEqual(self.client.get('/').status_code, 302)

    def test_next_goes_back_to_the_page_you_wanted(self):
        response = self.client.get('/history?tag=work&month=2026-11')
        self.assertEqual(login_target(response.location), '/history?tag=work&month=2026-11')
        page = self.client.get(response.location).text
        self.assertIn('name="next" value="/history?tag=work&amp;month=2026-11"', page)
        response = self.login('ann@example.com', TEST_PASSWORD, next='/history?tag=work&month=2026-11')
        self.assertEqual(response.location, '/history?tag=work&month=2026-11')

    def test_next_ignores_other_sites(self):
        unsafe = ['//evil.com', 'https://evil.com', 'http://evil.com/x', '/\\evil.com', '\\\\evil.com',
                  '/\t/evil.com', ' /evil', 'javascript:alert(1)', 'evil.com', '///evil.com']
        for target in unsafe:
            with self.subTest(target=target):
                client = self.app.test_client()
                page = client.get('/login', query_string={'next': target}).text
                self.assertNotIn('name="next"', page)
                response = self.login('ann@example.com', TEST_PASSWORD, client=client, next=target)
                self.assertEqual(response.location, '/')
                # Already logged in: /login?next= is still checked.
                response = client.get('/login', query_string={'next': target})
                self.assertEqual(response.location, '/')

    def test_logged_in_users_skip_the_login_and_signup_pages(self):
        log_in_as(self.client, self.user_id)
        self.assertEqual(self.client.get('/login').location, '/')
        self.assertEqual(self.client.get('/signup').location, '/')

    def test_a_deleted_account_is_logged_out(self):
        log_in_as(self.client, self.user_id)
        self.assertEqual(self.client.get('/').status_code, 200)
        db = connect(self.path)
        db.execute('DELETE FROM users WHERE id = ?', (self.user_id,))
        db.commit()
        db.close()
        self.assertEqual(self.client.get('/').status_code, 302)


class LoggedOutTests(AuthTestCase):
    def test_every_page_and_action_redirects_to_login(self):
        public = {'auth.login', 'auth.signup', 'auth.logout', 'static'}
        checked = set()
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint in public:
                continue
            url = rule.rule.replace('<int:entry_id>', '1')
            for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
                with self.subTest(url=url, method=method):
                    response = self.client.open(url, method=method)
                    self.assertEqual(response.status_code, 302)
                    # GET pages come back after logging in; form posts don't.
                    self.assertEqual(login_target(response.location), url if method == 'GET' else None)
                    checked.add(rule.endpoint)
        self.assertLessEqual({'entries.home', 'entries.write_journal', 'entries.view_entry',
                              'entries.edit_entry', 'entries.toggle_favorite', 'entries.delete_entry',
                              'entries.restore_entry', 'entries.trash', 'entries.purge_trashed_entry',
                              'entries.empty_trash_route', 'history.history', 'insights.insights',
                              'insights.week', 'backup.backup_page', 'backup.export_markdown',
                              'backup.export_json', 'backup.restore_backup'}, checked)

    def test_public_pages(self):
        for url in ['/login', '/signup', '/static/style.css']:
            self.assertEqual(self.client.get(url).status_code, 200)
        missing = self.client.get('/no-such-page')
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn('class="sidebar"', missing.text)


class IsolationTests(AuthTestCase):
    """User B must never see, change or export anything of user A's."""

    def setUp(self):
        super().setUp()
        self.a, self.a_id = signed_in_client(self.app, self.path, 'alice@example.com')
        self.b, self.b_id = signed_in_client(self.app, self.path, 'bob@example.com')
        today = date.today()
        self.year_ago = subtract_months(today, 12)
        add = self.add
        # A: today, a year ago (On this day), a favorite, one in Trash, all tagged.
        self.a_today = add(self.a_id, f'{today.isoformat()}T08:00:00', 'ALPHA today secret',
                           'Stressed', ['alpha-tag', 'shared-name'], favorite=1, went_well='ALPHA win')
        self.a_old = add(self.a_id, f'{self.year_ago.isoformat()}T09:00:00', 'ALPHA memory secret',
                         'Stressed', ['alpha-tag'])
        self.a_trash = add(self.a_id, '2026-10-05T09:00:00', 'ALPHA trashed secret', 'Stressed',
                           ['alpha-tag'], deleted_at='2026-11-01T09:00:00')
        # B: one ordinary entry, so B's pages aren't just empty states.
        self.b_entry = add(self.b_id, f'{today.isoformat()}T07:00:00', 'BRAVO note', 'Happy', ['shared-name'])

    def add(self, user_id, created_at, content, mood, tags, favorite=0, went_well=None, deleted_at=None):
        db = connect(self.path)
        try:
            cursor = db.execute(
                'INSERT INTO entries (user_id, title, content, mood, created_at, is_favorite, went_well, deleted_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (user_id, content + ' title', content, mood, created_at, favorite, went_well, deleted_at))
            set_entry_tags(db, cursor.lastrowid, tags, user_id=user_id)
            db.commit()
            return cursor.lastrowid
        finally:
            db.close()

    def a_rows(self):
        return self.query('SELECT * FROM entries WHERE user_id = ? ORDER BY id', self.a_id)

    def assert_no_alpha(self, text, where, typed_tag=False):
        """No trace of A's entries or tags. On pages where B typed "alpha-tag"
        into the filter, the echoed filter is B's own; only pills and
        suggestions would mean A's tag leaked."""
        self.assertNotIn('ALPHA', text, where)
        self.assertNotIn('<span aria-hidden="true">#</span>alpha-tag', text, where)
        self.assertNotIn('<option value="alpha-tag">', text, where)
        if not typed_tag:
            self.assertNotIn('alpha-tag', text, where)

    def test_b_sees_nothing_of_a_anywhere(self):
        b = self.b
        pages = {
            'home (recent, today card, on this day, two weeks)': '/',
            'history': '/history',
            'history month with A entries': '/history?month=2026-10',
            'history a year ago': f"/history?month={self.year_ago.strftime('%Y-%m')}",
            'calendar day filter': '/history?filter_date=2026-10-05&month=2026-10',
            'search': '/history?search=secret',
            'search by tag name': '/history?search=alpha',
            'tag filter': '/history?tag=alpha-tag',
            'favorites': '/history?favorites=1',
            'insights': '/insights',
            'week': '/week',
            'week of the trashed entry': '/week?start=2026-10-05',
            'trash': '/trash',
            'write (tag suggestions)': '/write',
            'backup page': '/backup',
            'markdown export': '/backup/export.md',
            'json export': '/backup/export.json',
        }
        # B's own entry pages: the Older / Newer links must skip A's entries.
        pages['own entry (pager)'] = f'/entry/{self.b_entry}'
        pages['own edit page'] = f'/edit/{self.b_entry}'
        for where, url in pages.items():
            with self.subTest(where=where):
                response = b.get(url)
                self.assertEqual(response.status_code, 200)
                text = response.get_data(as_text=True)
                self.assert_no_alpha(text, where, typed_tag='alpha' in url)
                for entry_id in (self.a_today, self.a_old, self.a_trash):
                    self.assertNotIn(f'/entry/{entry_id}"', text, where)
                    self.assertNotIn(f'id="entry-{entry_id}"', text, where)

        # The calendar has no dot on A's days, and B's own entry still shows.
        for day in (self.year_ago, date(2026, 10, 5)):
            history = b.get('/history', query_string={'month': day.strftime('%Y-%m')}).text
            link = re.search(r'<a href="[^"]*filter_date=%s[^"]*"\s+class="([^"]*)"' % day.isoformat(), history)
            self.assertNotIn('has-entry', link.group(1), day)
            self.assertNotIn('mood-stressed', link.group(1), day)
        a_history = self.a.get('/history', query_string={'month': self.year_ago.strftime('%Y-%m')}).text
        self.assertRegex(a_history, r'filter_date=%s[^"]*"\s+class="[^"]*has-entry' % self.year_ago.isoformat())
        self.assertIn('BRAVO note', b.get('/history').text)
        self.assertEqual(set(map(int, re.findall(r'id="entry-(\d+)"', b.get('/history?search=note').text))),
                         {self.b_entry})
        # Counts are B's alone.
        self.assertIn('1 entry so far.', b.get('/history').text)
        self.assertNotIn('Trash (', b.get('/history').text)
        insights = b.get('/insights').text
        self.assertRegex(insights, r'<p class="stat-value">1</p>\s*<h2 class="stat-label">Entries in total')
        self.assertRegex(insights, r'<p class="stat-value">1 <span>day</span></p>\s*<h2 class="stat-label">Current streak')
        self.assertIn('0.0%', insights)                       # A's reflection doesn't count
        home = b.get('/').text
        self.assertRegex(home, r'<dt>All time</dt>\s*<dd>1 entry</dd>')
        self.assertRegex(insights, r'(?s)Stressed.*?mood-bar-count">0<')
        self.assertRegex(insights, r'(?s)Happy.*?mood-bar-count">1<')
        self.assertIn('<p class="stat-value">1</p>', b.get('/backup').text)
        self.assertEqual([e['content'] for e in b.get('/backup/export.json').get_json()['entries']],
                         ['BRAVO note'])
        self.assertNotIn('On this day', b.get('/').text)
        self.assertIn('Trash is empty', b.get('/trash').text)
        # And A still sees all of it.
        self.assertIn('ALPHA memory secret', self.a.get('/').text)
        self.assertIn('ALPHA trashed secret', self.a.get('/trash').text)
        self.assertEqual(len(self.a.get('/backup/export.json').get_json()['entries']), 2)

    def test_b_gets_404_for_every_url_of_an_a_entry(self):
        before = self.a_rows()
        b = self.b
        for entry_id in (self.a_today, self.a_trash):
            requests = [
                ('GET', f'/entry/{entry_id}', None),
                ('GET', f'/edit/{entry_id}', None),
                ('POST', f'/edit/{entry_id}', {'journal': 'Taken over', 'tags': 'b-tag'}),
                ('POST', f'/favorite/{entry_id}', {}),
                ('POST', f'/delete/{entry_id}', {}),
                ('POST', f'/restore/{entry_id}', {'next': '/history'}),
                ('POST', f'/trash/{entry_id}/purge', {}),
            ]
            for method, url, data in requests:
                with self.subTest(method=method, url=url):
                    response = b.open(url, method=method, data=data)
                    self.assertEqual(response.status_code, 404)
                    self.assert_no_alpha(response.text, url)
        # Emptying B's Trash leaves A's Trash alone.
        b.post('/trash/empty')
        self.assertEqual(self.a_rows(), before)
        self.assertEqual(self.query('SELECT COUNT(*) FROM tags WHERE user_id = ?', self.a_id)[0][0], 2)
        # A's own requests still work.
        self.assertEqual(self.a.get(f'/entry/{self.a_today}').status_code, 200)
        self.assertEqual(self.a.post(f'/restore/{self.a_trash}').status_code, 302)

    def test_missing_entry_is_the_same_404(self):
        missing = self.b.get('/entry/99999')
        other = self.b.get(f'/entry/{self.a_today}')
        self.assertEqual((missing.status_code, missing.text), (other.status_code, other.text))

    def test_restore_gives_entries_to_the_restorer_and_checks_only_their_duplicates(self):
        exported = self.a.get('/backup/export.json').data
        self.assertNotIn(b'user_id', exported)
        page = self.b.post('/backup/restore', data={'backup_file': (io.BytesIO(exported), 'a.json')},
                           content_type='multipart/form-data', follow_redirects=True).text
        # A's identical entries don't count as duplicates for B.
        self.assertIn('Restored 2 entries.', page)
        self.assertEqual(len(self.b.get('/backup/export.json').get_json()['entries']), 3)
        self.assertEqual(len(self.a_rows()), 3)
        owners = self.query("SELECT DISTINCT user_id FROM entries WHERE content LIKE 'ALPHA%' AND id > ?",
                            self.b_entry)
        self.assertEqual(owners, [(self.b_id,)])
        # The restored tags are B's own tags, not links to A's.
        restored_tag_owners = self.query("""
            SELECT DISTINCT tags.user_id FROM entry_tags
            JOIN tags ON tags.id = entry_tags.tag_id
            JOIN entries ON entries.id = entry_tags.entry_id
            WHERE entries.user_id = ?""", self.b_id)
        self.assertEqual(restored_tag_owners, [(self.b_id,)])
        page = self.b.post('/backup/restore', data={'backup_file': (io.BytesIO(exported), 'a.json')},
                           content_type='multipart/form-data', follow_redirects=True).text
        self.assertIn('Nothing new to restore. All 2 entries were already here.', page)

    def test_tags_with_the_same_name_are_separate(self):
        rows = self.query("SELECT user_id FROM tags WHERE name = 'shared-name' ORDER BY user_id")
        self.assertEqual(rows, [(self.a_id,), (self.b_id,)])
        # B's tag filter and suggestions only know B's entries.
        ids = set(map(int, re.findall(r'id="entry-(\d+)"', self.b.get('/history?tag=shared-name').text)))
        self.assertEqual(ids, {self.b_entry})
        # A dropping the tag leaves B's tag and link alone.
        self.a.post(f'/edit/{self.a_today}', data={'journal': 'ALPHA today secret', 'tags': 'alpha-tag'})
        self.assertEqual(self.query("SELECT user_id FROM tags WHERE name = 'shared-name'"), [(self.b_id,)])
        self.assertIn('#</span>shared-name', self.b.get(f'/entry/{self.b_entry}').text)
        # And B writing a new "alpha-tag" makes a tag of B's own.
        self.b.post('/write', data={'journal': 'Mine', 'tags': 'alpha-tag'})
        rows = self.query("SELECT user_id FROM tags WHERE name = 'alpha-tag' ORDER BY user_id")
        self.assertEqual(rows, [(self.a_id,), (self.b_id,)])

    def test_new_entries_belong_to_the_writer(self):
        response = self.b.post('/write', data={'journal': 'Fresh', 'tags': 'new'})
        entry_id = int(response.location.split('/entry/')[1].split('?')[0])
        self.assertEqual(self.query('SELECT user_id FROM entries WHERE id = ?', entry_id), [(self.b_id,)])
        self.assertEqual(self.a.get(f'/entry/{entry_id}').status_code, 404)

    def test_draft_key_includes_the_user_id(self):
        a_page = self.a.get('/write').text
        b_page = self.b.get('/write').text
        self.assertIn(f'data-draft-key="my-journal-new-entry-draft-{self.a_id}"', a_page)
        self.assertIn(f'data-draft-key="my-journal-new-entry-draft-{self.b_id}"', b_page)
        saved = self.b.get(f'/entry/{self.b_entry}?saved=1').text
        self.assertIn(f'localStorage.removeItem("my-journal-new-entry-draft-{self.b_id}")', saved)
        self.assertNotIn('"my-journal-new-entry-draft"', a_page + b_page + saved)


class AssignEntriesTests(AuthTestCase):
    def test_assign_moves_only_unowned_rows_and_merges_tags(self):
        owner = add_user(self.path, 'owner@example.com')
        other = add_user(self.path, 'other@example.com')
        db = connect(self.path)
        try:
            # Owner already has a "work" tag; Other has an entry of their own.
            mine = db.execute("INSERT INTO entries (user_id, content, created_at) VALUES (?, 'Mine', '2026-11-01')",
                              (owner,)).lastrowid
            set_entry_tags(db, mine, ['work'], user_id=owner)
            theirs = db.execute("INSERT INTO entries (user_id, content, created_at) VALUES (?, 'Theirs', '2026-11-01')",
                                (other,)).lastrowid
            set_entry_tags(db, theirs, ['old'], user_id=other)
            # Two entries from before accounts, with tags of their own.
            old_ids = [db.execute("INSERT INTO entries (content, created_at) VALUES (?, '2026-01-01')",
                                  (text,)).lastrowid for text in ('Old one', 'Old two')]
            db.execute("INSERT INTO tags (id, user_id, name) VALUES (100, NULL, 'work'), (101, NULL, 'old')")
            db.executemany('INSERT INTO entry_tags VALUES (?, ?)',
                           [(old_ids[0], 100), (old_ids[1], 100), (old_ids[1], 101)])
            db.commit()
        finally:
            db.close()

        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['assign-entries', '--email', ' Owner@Example.com'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Assigned 2 entries and 2 tags to owner@example.com.', result.output)

        self.assertEqual(self.query('SELECT COUNT(*) FROM entries WHERE user_id IS NULL')[0][0], 0)
        self.assertEqual(self.query('SELECT COUNT(*) FROM tags WHERE user_id IS NULL')[0][0], 0)
        self.assertEqual(self.query('SELECT user_id FROM entries WHERE id = ?', theirs), [(other,)])
        self.assertEqual(self.query("SELECT user_id FROM tags WHERE name = 'old' ORDER BY user_id"),
                         [(owner,), (other,)])
        # The unowned "work" was merged into the owner's existing "work".
        [(work_id,)] = self.query("SELECT id FROM tags WHERE name = 'work'")
        self.assertEqual(sorted(r[0] for r in self.query('SELECT entry_id FROM entry_tags WHERE tag_id = ?', work_id)),
                         sorted([mine, *old_ids]))
        client = self.app.test_client()
        log_in_as(client, owner)
        ids = set(map(int, re.findall(r'id="entry-(\d+)"', client.get('/history?tag=work').text)))
        self.assertEqual(ids, {mine, *old_ids})

        again = runner.invoke(args=['assign-entries', '--email', 'owner@example.com'])
        self.assertIn('Assigned 0 entries and 0 tags', again.output)

    def test_unknown_email_changes_nothing(self):
        db = connect(self.path)
        db.execute("INSERT INTO entries (content, created_at) VALUES ('Old', '2026-01-01')")
        db.commit()
        db.close()
        result = self.app.test_cli_runner().invoke(args=['assign-entries', '--email', 'ghost@example.com'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('No account uses ghost@example.com', result.output)
        self.assertEqual(self.query('SELECT user_id FROM entries'), [(None,)])


class UpgradeTests(unittest.TestCase):
    def test_tags_become_per_user_keeping_ids_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'old.db')
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                                      created_at TEXT NOT NULL, mood TEXT);
                CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE entry_tags (
                    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (entry_id, tag_id));
                INSERT INTO entries (id, content, created_at) VALUES (1, 'One', '2026-01-01'), (2, 'Two', '2026-01-02');
                INSERT INTO tags VALUES (7, 'work'), (9, 'home');
                INSERT INTO entry_tags VALUES (1, 7), (2, 7), (2, 9);
            """)
            db.close()
            initialize_database(path)
            initialize_database(path)    # safe to run again
            db = connect(path)
            try:
                self.assertEqual(db.execute('SELECT id, user_id, name FROM tags ORDER BY id').fetchall(),
                                 [(7, None, 'work'), (9, None, 'home')])
                self.assertEqual(db.execute('SELECT * FROM entry_tags ORDER BY entry_id, tag_id').fetchall(),
                                 [(1, 7), (2, 7), (2, 9)])
                self.assertEqual(db.execute('SELECT user_id FROM entries').fetchall(), [(None,), (None,)])
                self.assertIn('UNIQUE (user_id, name)',
                              db.execute("SELECT sql FROM sqlite_master WHERE name = 'tags'").fetchone()[0])
                self.assertIn('entries_by_user',
                              [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
