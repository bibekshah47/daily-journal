"""Account settings, revocable sessions, forgot/reset password, and the mailer."""
import contextlib
from datetime import datetime
import io
import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from accounts import make_reset_token
from database import connect, initialize_database
import mailer
import security
from tags import set_entry_tags
from tests.helpers import SECURITY_OFF, TEST_EMAIL, TEST_PASSWORD, add_user, log_in_as, new_test_database, skip_daily_maintenance, sqlite_only

SECRET = 'settings-test-secret-' + 'y' * 40
NEW_PASSWORD = 'a-brand-new-password'


def strip_random(text):
    """Remove per-response values (CSP nonces, CSRF tokens)."""
    return re.sub(r'(nonce|name="csrf_token" value)="[^"]+"', '', text)


class AccountTestCase(unittest.TestCase):
    overrides = SECURITY_OFF

    def setUp(self):
        import app as module
        self.path = new_test_database(self)
        self.outbox = []
        self.app = module.create_app({'TESTING': True, 'DATABASE': self.path, 'SECRET_KEY': SECRET,
                                      'MAIL_OUTBOX': self.outbox, **self.overrides})
        skip_daily_maintenance(module, self.path)
        security.limiter.reset()
        security.reset_forgot_limits()
        self.user_id = add_user(self.path, display_name='Ann Lee')
        self.client = self.app.test_client()
        log_in_as(self.client, self.user_id)

    def query(self, sql, *params):
        db = connect(self.path)
        try:
            return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def user_row(self, user_id=None):
        rows = self.query('SELECT email, display_name, session_version, password_hash FROM users '
                          'WHERE id = ?', user_id or self.user_id)
        return rows[0] if rows else None

    def login(self, email=TEST_EMAIL, password=TEST_PASSWORD, client=None, **data):
        client = client or self.app.test_client()
        response = client.post('/login', data={'email': email, 'password': password, **data})
        return client, response

    def is_logged_in(self, client):
        return client.get('/').status_code == 200

    def add_entry(self, user_id, tags=()):
        db = connect(self.path)
        try:
            entry_id = db.insert('INSERT INTO entries (content, created_at, user_id) VALUES (?, ?, ?)',
                                ('Text', datetime.now().isoformat(), user_id))
            if tags:
                set_entry_tags(db, entry_id, list(tags), user_id=user_id)
            db.commit()
            return entry_id
        finally:
            db.close()


def sections(page):
    """{section: (is_open, html)} for each collapsed <details> on Settings."""
    found = {}
    for match in re.finditer(r'<details class="settings-[a-z]+" name="settings-section" '
                             r'id="settings-([a-z]+)"([^>]*)>(.*?)</details>', page, re.S):
        found[match.group(1)] = ('open' in match.group(2).split(), match.group(3))
    return found


def outside_sections(page):
    return re.sub(r'<details\b.*?</details>', '', page, flags=re.S)


class SettingsPageTests(AccountTestCase):
    def test_page_shows_compact_rows_all_closed(self):
        db = connect(self.path)
        db.execute("UPDATE users SET password_changed_at = '2026-03-04T10:00:00'")
        db.commit()
        db.close()
        page = self.client.get('/settings').text
        rows = re.findall(r'<span class="settings-label">([^<]+)<span class="visually-hidden">:</span>'
                          r'</span>\s*(?:<span class="settings-value[^"]*">([^<]*)</span>)?', page)
        self.assertEqual(rows, [('Name', 'Ann Lee'), ('Email', TEST_EMAIL),
                                ('Password', 'Last changed March 4, 2026'),
                                ('Time zone', 'UTC')])   # this test user has none stored
        self.assertEqual(page.count('<span class="settings-change">Change</span>'), 4)
        found = sections(page)
        self.assertEqual(list(found), ['name', 'email', 'password', 'timezone', 'delete'])
        self.assertFalse(any(is_open for is_open, _ in found.values()))
        self.assertIn('<summary class="settings-delete-toggle">Delete account…</summary>', page)
        self.assertIn('Download your entries any time from', page)
        self.assertIn('href="/backup">Backup &amp; export</a>.', page)
        # Name, Email, Password, Time zone and Delete forms, plus the sidebar's Log out.
        self.assertEqual(page.count('name="csrf_token"'), 6)
        self.assertIn('id="delete-account-dialog"', page)
        self.assertIn('Save', found['name'][1])
        self.assertIn('Cancel', found['name'][1])

    def test_password_change_records_the_date(self):
        self.client.post('/settings/password', data={'current_password': TEST_PASSWORD,
                                                     'new_password': NEW_PASSWORD})
        from views.shared import friendly_date
        self.assertIn('Last changed ' + friendly_date(datetime.now()), self.client.get('/settings').text)

    def test_unknown_password_date_and_missing_name(self):
        db = connect(self.path)
        db.execute('UPDATE users SET password_changed_at = NULL, display_name = NULL')
        db.commit()
        db.close()
        page = self.client.get('/settings').text
        self.assertIn('<span class="settings-value is-empty">Not set</span>', page)
        self.assertNotIn('Last changed', page)

    def test_inputs_start_right(self):
        page = self.client.get('/settings').text
        name = re.search(r'<input type="text" id="display_name"[^>]*>', page).group(0)
        self.assertIn('value="Ann Lee"', name)
        email = re.search(r'<input type="email" id="new_email"[^>]*>', page, re.S).group(0)
        self.assertIn('value=""', email)
        self.assertIn('placeholder="new@example.com"', email)
        self.assertIn('autocomplete="off"', email)
        for field_id, autocomplete in (('email_password', 'current-password'),
                                       ('current_password', 'current-password'),
                                       ('delete_password', 'current-password'),
                                       ('new_password', 'new-password')):
            with self.subTest(field=field_id):
                tag = re.search(rf'<input type="password" id="{field_id}"[^>]*>', page, re.S).group(0)
                self.assertIn(f'autocomplete="{autocomplete}"', tag)

    def test_password_inputs_only_inside_closed_sections(self):
        page = self.client.get('/settings').text
        self.assertNotIn('type="password"', outside_sections(page))
        self.assertEqual(sum(html.count('type="password"') for _, html in sections(page).values()), 4)

    def assert_only_open(self, response, section):
        found = sections(response.text)
        self.assertEqual([name for name, (is_open, _) in found.items() if is_open], [section])
        return found[section][1]

    def test_sections_open_on_their_errors(self):
        html = self.assert_only_open(self.client.post('/settings/name', data={'display_name': 'x' * 41}), 'name')
        self.assertIn('Use 40 characters or fewer', html)
        self.assertIn(f'value="{"x" * 41}"', html)    # what was typed, to fix it
        html = self.assert_only_open(self.client.post('/settings/email', data={
            'new_email': 'typed@example.com', 'email_password': 'wrong'}), 'email')
        self.assertIn("That password isn&#39;t right.", html)
        self.assertIn('value="typed@example.com"', html)
        html = self.assert_only_open(self.client.post('/settings/password', data={
            'current_password': TEST_PASSWORD, 'new_password': 'short'}), 'password')
        self.assertIn('Use at least 8 characters', html)
        html = self.assert_only_open(self.client.post('/settings/delete', data={
            'delete_password': TEST_PASSWORD, 'delete_confirm': 'nope'}), 'delete')
        self.assertIn('Type DELETE in capital letters to confirm', html)
        self.assertNotIn(TEST_PASSWORD, html)

    def test_success_closes_every_section(self):
        self.client.post('/settings/name', data={'display_name': 'Annie'})
        page = self.client.get('/settings').text
        self.assertFalse(any(is_open for is_open, _ in sections(page).values()))
        self.assertIn('<span class="settings-value">Annie</span>', page)

    def test_sidebar_and_home_link_to_settings(self):
        home = self.client.get('/').text
        link = re.search(r'<a class="sidebar-settings[^"]*" href="/settings".*?</a>', home, re.S).group(0)
        self.assertIn('data-lucide="settings"', link)
        self.assertIn('<span class="sidebar-name" title="Ann Lee">Ann Lee</span>', link)
        self.assertLess(home.index('class="sidebar-settings'), home.index('sidebar-logout'))
        self.assertRegex(home, r'<a href="/settings" class="mobile-settings">\s*'
                               r'<i data-lucide="settings" aria-hidden="true"></i>Settings</a>')
        settings_page = self.client.get('/settings').text
        self.assertRegex(settings_page, r'class="sidebar-settings active" href="/settings"\s+'
                                        r'title="Settings" aria-current="page"')

    def test_requires_login(self):
        response = self.app.test_client().get('/settings')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)


class NameTests(AccountTestCase):
    def test_change_name(self):
        response = self.client.post('/settings/name', data={'display_name': '  Annie   B '},
                                    follow_redirects=True)
        self.assertIn('Name saved.', response.text)
        self.assertEqual(self.user_row()[1], 'Annie B')

    def test_blank_name_removes_it(self):
        self.client.post('/settings/name', data={'display_name': '   '})
        self.assertIsNone(self.user_row()[1])

    def test_too_long_name_is_refused(self):
        response = self.client.post('/settings/name', data={'display_name': 'x' * 41})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Use 40 characters or fewer', response.text)
        self.assertEqual(self.user_row()[1], 'Ann Lee')


class EmailTests(AccountTestCase):
    def change(self, email, password=TEST_PASSWORD):
        return self.client.post('/settings/email', data={'new_email': email, 'email_password': password})

    def test_change_email_stores_lowercase(self):
        response = self.change('  New.Ann@Example.COM ')
        self.assertEqual(response.status_code, 302)
        self.assertIn('Email changed.', self.client.get('/settings').text)
        self.assertEqual(self.user_row()[0], 'new.ann@example.com')
        self.assertEqual(self.login('new.ann@example.com')[1].status_code, 302)
        self.assertTrue(self.is_logged_in(self.client))   # this session is unaffected

    def test_wrong_password_is_refused(self):
        response = self.change('new@example.com', 'not my password')
        self.assertEqual(response.status_code, 400)
        self.assertIn("That password isn&#39;t right.", response.text)
        self.assertIn('id="email_password-error"', response.text)
        self.assertEqual(self.user_row()[0], TEST_EMAIL)

    def test_email_used_by_someone_else_is_refused(self):
        add_user(self.path, 'taken@example.com')
        response = self.change('Taken@example.com')
        self.assertEqual(response.status_code, 400)
        self.assertIn('An account with this email already exists', response.text)
        self.assertEqual(self.user_row()[0], TEST_EMAIL)

    def test_invalid_email_is_refused(self):
        for email in ('', 'not an email', 'a@b'):
            with self.subTest(email=email):
                response = self.change(email)
                self.assertEqual(response.status_code, 400)
                self.assertIn('Enter a valid email', response.text)

    def test_same_email_is_refused(self):
        self.assertIn('already your email', self.change(TEST_EMAIL.upper()).text)


class PasswordTests(AccountTestCase):
    def change(self, current=TEST_PASSWORD, new=NEW_PASSWORD, client=None):
        return (client or self.client).post('/settings/password',
                                            data={'current_password': current, 'new_password': new})

    def test_change_password_logs_out_other_sessions_only(self):
        other_device, _ = self.login()
        remembered, _ = self.login(remember='1')
        remember_cookie = remembered.get_cookie('remember_token').value
        self.assertTrue(self.is_logged_in(other_device))

        response = self.change()
        self.assertEqual(response.status_code, 302)
        self.assertIn("Password changed. You&#39;ve been logged out on your other devices.",
                      self.client.get('/settings').text)
        self.assertEqual(self.user_row()[2], 1)

        self.assertTrue(self.is_logged_in(self.client))        # this device stays in
        self.assertFalse(self.is_logged_in(other_device))      # other sessions end
        old_cookie = self.app.test_client()
        old_cookie.set_cookie('remember_token', remember_cookie)
        self.assertFalse(self.is_logged_in(old_cookie))        # and old remember cookies

        self.assertEqual(self.login(password=TEST_PASSWORD)[1].status_code, 400)
        self.assertEqual(self.login(password=NEW_PASSWORD)[1].status_code, 302)

    def test_keep_me_logged_in_survives_on_this_device(self):
        client, _ = self.login(remember='1')
        self.change(client=client)
        cookie = client.get_cookie('remember_token').value
        fresh = self.app.test_client()
        fresh.set_cookie('remember_token', cookie)
        self.assertTrue(self.is_logged_in(fresh))

    def test_wrong_current_password_is_refused(self):
        response = self.change(current='nope nope nope')
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="current_password-error"', response.text)
        self.assertIn("That password isn&#39;t right.", response.text)
        self.assertEqual(self.user_row()[2], 0)

    def test_new_password_length_rules(self):
        for new, message in (('short', 'Use at least 8 characters'),
                             ('x' * 129, 'Use 128 characters or fewer')):
            with self.subTest(new=new[:10]):
                response = self.change(new=new)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.text)
        self.assertEqual(self.user_row()[2], 0)

    def test_page_never_echoes_passwords(self):
        response = self.change(current='typed-secret-1', new='x')
        self.assertNotIn('typed-secret-1', response.text)

    def test_legacy_session_without_a_version_works_until_the_first_change(self):
        legacy = self.app.test_client()
        log_in_as(legacy, self.user_id)
        with legacy.session_transaction() as session:
            session['_user_id'] = str(self.user_id)   # from before session versions
        self.assertTrue(self.is_logged_in(legacy))
        self.change()
        self.assertFalse(self.is_logged_in(legacy))


class DeleteAccountTests(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.mine = [self.add_entry(self.user_id, ['work', 'home']), self.add_entry(self.user_id)]
        self.other_id = add_user(self.path, 'other@example.com')
        self.theirs = self.add_entry(self.other_id, ['work'])

    def delete(self, password=TEST_PASSWORD, confirm='DELETE'):
        return self.client.post('/settings/delete', data={'delete_password': password,
                                                          'delete_confirm': confirm})

    def counts(self, user_id):
        return (
            self.query('SELECT COUNT(*) FROM users WHERE id = ?', user_id)[0][0],
            self.query('SELECT COUNT(*) FROM entries WHERE user_id = ?', user_id)[0][0],
            self.query('SELECT COUNT(*) FROM tags WHERE user_id = ?', user_id)[0][0],
            self.query('SELECT COUNT(*) FROM entry_tags JOIN entries ON entries.id = entry_id '
                       'WHERE entries.user_id = ?', user_id)[0][0],
        )

    def test_delete_erases_the_account_and_only_its_data(self):
        self.assertEqual(self.counts(self.user_id), (1, 2, 2, 2))
        response = self.delete()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/login'))
        self.assertEqual(self.counts(self.user_id), (0, 0, 0, 0))
        self.assertEqual(self.query('SELECT COUNT(*) FROM entry_tags')[0][0], 1)
        self.assertEqual(self.counts(self.other_id), (1, 1, 1, 1))

        login_page = self.client.get('/login').text
        self.assertIn('Your account and journal were deleted.', login_page)
        self.assertIn(f'localStorage.removeItem("my-journal-new-entry-draft-{self.user_id}")', login_page)
        self.assertNotIn('localStorage.removeItem', self.client.get('/login').text)   # only once
        self.assertEqual(self.client.get('/').status_code, 302)                        # logged out
        self.assertEqual(self.login()[1].status_code, 400)                             # account gone

    def test_wrong_password_refuses(self):
        response = self.delete(password='wrong password')
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="delete_password-error"', response.text)
        self.assertEqual(self.counts(self.user_id), (1, 2, 2, 2))

    def test_missing_or_wrong_confirmation_refuses(self):
        for confirm in ('', 'delete', 'DELET', 'yes'):
            with self.subTest(confirm=confirm):
                response = self.delete(confirm=confirm)
                self.assertEqual(response.status_code, 400)
                self.assertIn('Type DELETE in capital letters to confirm', response.text)
                self.assertEqual(self.counts(self.user_id), (1, 2, 2, 2))
        self.assertTrue(self.is_logged_in(self.client))


class DeletedIdTests(AccountTestCase):
    """A deleted account's id must never reach a new account."""

    def test_ids_are_not_reused_and_stale_logins_and_links_stay_dead(self):
        stale_device = self.app.test_client()
        log_in_as(stale_device, self.user_id)
        stale_link = make_reset_token(SECRET, self.user_id, 0)
        self.client.post('/settings/delete', data={'delete_password': TEST_PASSWORD,
                                                   'delete_confirm': 'DELETE'})
        # The deleted user had the highest id: a plain INTEGER PRIMARY KEY would reuse it.
        signup = self.app.test_client().post('/signup', data={'email': 'new@example.com',
                                                              'password': NEW_PASSWORD})
        self.assertEqual(signup.status_code, 302)
        new_id = self.query("SELECT id FROM users WHERE email = 'new@example.com'")[0][0]
        self.assertGreater(new_id, self.user_id)
        self.assertFalse(self.is_logged_in(stale_device))
        self.assertEqual(self.app.test_client().get(f'/reset/{stale_link}').status_code, 400)


@sqlite_only
class UsersUpgradeTests(unittest.TestCase):
    def test_old_users_table_is_upgraded_keeping_ids_and_entries(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, 'old.db')
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                                password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                                display_name TEXT);
            INSERT INTO users VALUES (3, 'a@example.com', 'h', '2026-01-01', 'A');
            INSERT INTO users VALUES (7, 'b@example.com', 'h', '2026-01-01', NULL);
        """)
        db.close()
        initialize_database(path)
        db = connect(path)
        db.execute("INSERT INTO entries (content, created_at, user_id) VALUES ('x', '2026', 7)")
        db.commit()
        db.close()
        initialize_database(path)   # running again changes nothing

        rows = self.query(path, 'SELECT id, email, display_name, session_version FROM users ORDER BY id')
        self.assertEqual(rows, [(3, 'a@example.com', 'A', 0), (7, 'b@example.com', None, 0)])
        self.assertIn('AUTOINCREMENT', self.query(
            path, "SELECT sql FROM sqlite_master WHERE name = 'users'")[0][0].upper())
        db = connect(path)
        with db:
            db.execute('DELETE FROM users WHERE id = 7')   # cascade still works
            new_id = db.insert("INSERT INTO users (email, password_hash, created_at) "
                                "VALUES ('c@example.com', 'h', '2026')")
        db.close()
        self.assertEqual(self.query(path, 'SELECT COUNT(*) FROM entries')[0][0], 0)
        self.assertEqual(new_id, 8)

    def query(self, path, sql):
        db = connect(path)
        try:
            return db.execute(sql).fetchall()
        finally:
            db.close()


class ForgotPasswordTests(AccountTestCase):
    def forgot(self, email, client=None, **extra):
        return (client or self.app.test_client()).post('/forgot', data={'email': email}, **extra)

    def test_login_page_links_to_forgot(self):
        self.assertIn('href="/forgot"', self.app.test_client().get('/login').text)
        self.assertEqual(self.app.test_client().get('/forgot').status_code, 200)

    def test_same_message_for_existing_and_unknown_emails(self):
        known = self.forgot(TEST_EMAIL.upper())
        unknown = self.forgot('nobody@example.com')
        self.assertEqual(known.status_code, unknown.status_code, 200)
        message = ("If an account exists for that email, we&#39;ve sent a link to reset your "
                   "password. It works for 1 hour.")
        self.assertIn(message, known.text)
        self.assertEqual(strip_random(known.text).replace(TEST_EMAIL, 'E'),
                         strip_random(unknown.text).replace('nobody@example.com', 'E'))

    def test_email_is_sent_only_for_existing_accounts(self):
        self.forgot('nobody@example.com')
        self.assertEqual(self.outbox, [])
        self.forgot(TEST_EMAIL)
        self.assertEqual(len(self.outbox), 1)
        mail = self.outbox[0]
        self.assertEqual(mail['to'], TEST_EMAIL)
        self.assertEqual(mail['subject'], 'Reset your Innerdesk password')
        self.assertIn('Hi Ann,', mail['body'])
        self.assertIn("If you didn't ask for this, you can ignore this email.", mail['body'])
        self.assertRegex(mail['body'], r'http://localhost/reset/[\w.-]+')

    def test_invalid_email_shows_a_field_error(self):
        response = self.forgot('not an email')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Enter a valid email', response.text)

    def test_link_uses_https_in_production(self):
        self.app.config['PREFERRED_URL_SCHEME'] = 'https'
        self.forgot(TEST_EMAIL)
        self.assertRegex(self.outbox[0]['body'], r'https://localhost/reset/')


class ForgotRateLimitTests(AccountTestCase):
    overrides = {'WTF_CSRF_ENABLED': False}   # rate limits on

    def forgot(self, email, ip='10.0.0.1'):
        return self.app.test_client().post('/forgot', data={'email': email},
                                           environ_base={'REMOTE_ADDR': ip})

    def test_three_per_hour_per_ip(self):
        for number in range(3):
            self.assertEqual(self.forgot(f'person{number}@example.com').status_code, 200)
        response = self.forgot('person9@example.com')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Too many attempts. Try again in a few minutes.', response.text)
        self.assertEqual(self.forgot('person9@example.com', ip='10.0.0.2').status_code, 200)

    def test_three_per_hour_per_email(self):
        for number in range(5):
            response = self.forgot(TEST_EMAIL, ip=f'10.0.1.{number}')
            self.assertEqual(response.status_code, 200)   # always the same confirmation
            self.assertIn('If an account exists for that email', response.text)
        self.assertEqual(len(self.outbox), 3)


class ResetPasswordTests(AccountTestCase):
    def token(self, version=0, user_id=None, secret=SECRET):
        return make_reset_token(secret, user_id or self.user_id, version)

    def reset(self, token, password=NEW_PASSWORD, client=None):
        return (client or self.app.test_client()).post(f'/reset/{token}', data={'password': password})

    def assert_refused(self, token):
        for response in (self.app.test_client().get(f'/reset/{token}'), self.reset(token)):
            self.assertEqual(response.status_code, 400)
            self.assertIn('This link has expired or was already used.', response.text)
            self.assertIn('href="/forgot"', response.text)

    def test_link_from_the_email_works_once(self):
        self.app.test_client().post('/forgot', data={'email': TEST_EMAIL})
        link = re.search(r'http://localhost(/reset/\S+)', self.outbox[0]['body']).group(1)
        other_device, _ = self.login()

        page = self.app.test_client().get(link)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Choose a new password', page.text)
        client = self.app.test_client()
        response = client.post(link, data={'password': NEW_PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertIn('Password updated.', client.get('/').text)    # logged in

        self.assertEqual(self.user_row()[2], 1)
        self.assertFalse(self.is_logged_in(other_device))
        self.assertFalse(self.is_logged_in(self.client))
        self.assertEqual(self.login(password=NEW_PASSWORD)[1].status_code, 302)
        self.assert_refused(link.rsplit('/', 1)[1])                  # reused

    def test_password_change_in_settings_kills_open_links(self):
        token = self.token()
        self.client.post('/settings/password', data={'current_password': TEST_PASSWORD,
                                                     'new_password': NEW_PASSWORD})
        self.assert_refused(token)

    def test_expired_link_is_refused(self):
        with mock.patch('time.time', return_value=time.time() - 3601):
            token = self.token()
        self.assert_refused(token)
        with mock.patch('time.time', return_value=time.time() - 3500):
            self.assertEqual(self.app.test_client().get(f'/reset/{self.token()}').status_code, 200)

    def test_tampered_links_are_refused(self):
        token = self.token()
        payload, timestamp, signature = token.split('.')
        swapped = 'A' if signature[0] != 'A' else 'B'
        for bad in (payload + '.' + timestamp + '.' + swapped + signature[1:],
                    self.token(secret='some-other-secret'),
                    make_reset_token(SECRET, self.user_id, 5),   # wrong version
                    make_reset_token(SECRET, 99999, 0),          # no such user
                    'not-a-token'):
            with self.subTest(token=bad[:20]):
                self.assert_refused(bad)
        self.assertEqual(self.user_row()[2], 0)

    def test_short_password_keeps_the_link_usable(self):
        token = self.token()
        response = self.reset(token, 'short')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Use at least 8 characters', response.text)
        self.assertEqual(self.reset(token).status_code, 302)

    def test_reset_clears_a_login_lockout(self):
        for _ in range(10):
            self.login(password='wrong guess')
        self.assertEqual(self.login(password=TEST_PASSWORD)[1].status_code, 429)
        self.reset(self.token())
        self.assertEqual(self.login(password=NEW_PASSWORD)[1].status_code, 302)


class MailerTests(unittest.TestCase):
    def setUp(self):
        import app as module
        self.app = module.app
        self.saved = dict(self.app.config)
        self.addCleanup(lambda: (self.app.config.clear(), self.app.config.update(self.saved)))
        self.app.config.update(MAIL_OUTBOX=None, MAIL_SERVER='', MAIL_USERNAME='', MAIL_PASSWORD='',
                               MAIL_FROM='', MAIL_PORT=587)

    def send(self):
        output = io.StringIO()
        with self.app.app_context(), contextlib.redirect_stdout(output), \
                mock.patch('smtplib.SMTP') as smtp:
            thread = mailer.send_email('ann@example.com', 'Hello', 'Body text')
            if thread:
                thread.join(5)
        return output.getvalue(), smtp

    def test_development_prints_instead_of_sending(self):
        self.app.config.update(APP_ENV='development', MAIL_SERVER='smtp.example.com')
        printed, smtp = self.send()
        smtp.assert_not_called()
        for part in ('To: ann@example.com', 'Subject: Hello', 'Body text'):
            self.assertIn(part, printed)

    def test_production_without_a_server_warns_and_never_prints_the_email(self):
        # The body of a reset email is a secret link: it must not reach the logs.
        self.app.config.update(APP_ENV='production')
        with self.assertLogs(self.app.logger, 'WARNING') as logs:
            printed, smtp = self.send()
        smtp.assert_not_called()
        self.assertEqual(printed, '')
        self.assertIn('was not sent', logs.output[0])
        self.assertNotIn('Body text', ' '.join(logs.output))

    def test_production_sends_with_starttls(self):
        self.app.config.update(APP_ENV='production', MAIL_SERVER='smtp.example.com', MAIL_PORT=2525,
                               MAIL_USERNAME='apikey', MAIL_PASSWORD='pw', MAIL_FROM='hi@example.com')
        printed, smtp = self.send()
        self.assertEqual(printed, '')
        smtp.assert_called_once_with('smtp.example.com', 2525, timeout=mailer.SMTP_TIMEOUT_SECONDS)
        connection = smtp.return_value.__enter__.return_value
        connection.starttls.assert_called_once()
        connection.login.assert_called_once_with('apikey', 'pw')
        message = connection.send_message.call_args[0][0]
        self.assertEqual(message['From'], 'Innerdesk <hi@example.com>')
        self.assertEqual(message['To'], 'ann@example.com')
        self.assertEqual(message.get_content().strip(), 'Body text')

    def test_send_failure_is_logged_not_raised(self):
        self.app.config.update(APP_ENV='production', MAIL_SERVER='smtp.example.com')
        with self.assertLogs(self.app.logger, 'ERROR') as logs, \
                mock.patch('smtplib.SMTP', side_effect=OSError('connection refused')):
            with self.app.app_context():
                mailer.send_email('ann@example.com', 'Hello', 'Body').join(5)
        self.assertIn('Could not send an email (Hello)', logs.output[0])
        self.assertNotIn('ann@example.com', ' '.join(logs.output))


class MailConfigTests(unittest.TestCase):
    def test_mail_settings_from_the_environment(self):
        from config import ConfigError, load_config
        settings = load_config({'MAIL_SERVER': ' smtp.example.com ', 'MAIL_USERNAME': 'u',
                                'MAIL_PASSWORD': 'p', 'MAIL_FROM': 'me@example.com',
                                'TRUSTED_HOSTS': 'journal.example.com, www.journal.example.com'})
        self.assertEqual(settings['MAIL_SERVER'], 'smtp.example.com')
        self.assertEqual(settings['MAIL_PORT'], 587)
        # Stored as ALLOWED_HOSTS: security.py checks it (production only),
        # not Flask's own TRUSTED_HOSTS, which would refuse health checks.
        self.assertEqual(settings['ALLOWED_HOSTS'], ['journal.example.com', 'www.journal.example.com'])
        self.assertNotIn('TRUSTED_HOSTS', settings)
        self.assertEqual(load_config({})['ALLOWED_HOSTS'], [])
        self.assertEqual(load_config({'MAIL_PORT': '2525'})['MAIL_PORT'], 2525)
        with self.assertRaisesRegex(ConfigError, 'MAIL_PORT'):
            load_config({'MAIL_PORT': 'abc'})


if __name__ == '__main__':
    unittest.main()
