"""CSRF, login rate limits and lockout, cookies, and security headers."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from config import load_config
from database import connect, initialize_database
import security
from tests.helpers import TEST_EMAIL, TEST_PASSWORD, add_user, log_in_as, new_test_database, skip_daily_maintenance

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'
SECRET = 'test-secret-' + 'x' * 40


class SecurityTestCase(unittest.TestCase):
    """Each test gets its own app, database and empty rate-limit counters."""

    overrides = {}

    def setUp(self):
        import app as module
        self.path = new_test_database(self)
        self.app = module.create_app({'TESTING': True, 'DATABASE': self.path,
                                      'SECRET_KEY': SECRET, **self.overrides})
        # The daily backup hook would otherwise copy the database on the first request.
        skip_daily_maintenance(module, self.path)
        security.limiter.reset()
        self.user_id = add_user(self.path)
        self.client = self.app.test_client()

    def login(self, email=TEST_EMAIL, password='wrong password', client=None, **extra):
        return (client or self.client).post('/login', data={'email': email, 'password': password},
                                            **extra)


def csrf_token(page_text):
    return re.search(r'name="csrf_token" value="([^"]+)"', page_text).group(1)


class CsrfTests(SecurityTestCase):
    overrides = {'RATELIMIT_ENABLED': False}   # CSRF stays on

    def test_post_without_a_token_gets_the_friendly_page(self):
        log_in_as(self.client, self.user_id)
        response = self.client.post('/write', data={'journal': 'Hello'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('This page was open too long', response.text)
        self.assertIn('Go back, reload, and try again.', response.text)
        self.assertIn('class="sidebar"', response.text)   # rendered inside base.html
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 0)

    def test_login_without_a_token_is_rejected(self):
        response = self.login(password=TEST_PASSWORD)
        self.assertEqual(response.status_code, 400)
        self.assertIn('This page was open too long', response.text)

    def test_post_with_the_token_from_the_form_works(self):
        token = csrf_token(self.client.get('/login').text)
        response = self.client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD,
                                                    'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        page = self.client.get('/write').text
        response = self.client.post('/write', data={'journal': 'Hello', 'csrf_token': csrf_token(page)})
        self.assertEqual(response.status_code, 302)

    def test_logged_out_post_still_goes_to_login(self):
        response = self.client.post('/write', data={'journal': 'Hello'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_every_post_form_has_a_token(self):
        for path in TEMPLATES.glob('*.html'):
            text = path.read_text()
            for form in re.finditer(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', text, re.I | re.S):
                with self.subTest(template=path.name, form=form.group(0)[:80]):
                    self.assertIn('name="csrf_token" value="{{ csrf_token() }}"', form.group(1))


class RateLimitTests(SecurityTestCase):
    overrides = {'WTF_CSRF_ENABLED': False}   # rate limits stay on

    def test_sixth_login_in_a_minute_is_refused(self):
        for attempt in range(5):
            self.assertEqual(self.login().status_code, 400, attempt)
        with self.assertLogs(self.app.logger, 'WARNING') as logs:
            response = self.login(password=TEST_PASSWORD)   # even the right password
        self.assertEqual(response.status_code, 429)
        self.assertIn('Email or password is incorrect. Too many attempts. Try again in a few minutes.',
                      response.text)
        self.assertIn('<h1 id="auth-heading">Log in</h1>', response.text)
        self.assertIn('Rate limit hit', ' '.join(logs.output))
        self.assertNotIn(TEST_EMAIL, ' '.join(logs.output))

    def test_viewing_the_login_page_is_not_limited(self):
        for _ in range(10):
            self.assertEqual(self.client.get('/login').status_code, 200)

    def test_sixth_signup_in_an_hour_is_refused(self):
        for attempt in range(5):
            response = self.client.post('/signup', data={'email': 'not-an-email', 'password': 'x'})
            self.assertEqual(response.status_code, 400, attempt)
        response = self.client.post('/signup', data={'email': 'new@example.com',
                                                     'password': 'long enough password'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Too many attempts. Try again in a few minutes.', response.text)
        self.assertIn('Create an account', response.text)

    def test_limits_are_per_ip(self):
        for _ in range(5):
            self.login(environ_base={'REMOTE_ADDR': '10.0.0.1'})
        self.assertEqual(self.login(environ_base={'REMOTE_ADDR': '10.0.0.1'}).status_code, 429)
        self.assertEqual(self.login(environ_base={'REMOTE_ADDR': '10.0.0.2'}).status_code, 400)

    def test_forwarded_for_is_ignored_outside_production(self):
        for number in range(6):
            response = self.login(headers={'X-Forwarded-For': f'203.0.113.{number}'})
        self.assertEqual(response.status_code, 429)


class ProductionProxyTests(SecurityTestCase):
    overrides = {'WTF_CSRF_ENABLED': False, 'APP_ENV': 'production'}

    def test_forwarded_for_is_the_client_ip_in_production(self):
        # As Render's proxy sends them (HTTPS, so no redirect to https).
        def via_proxy(ip):
            return {'X-Forwarded-For': ip, 'X-Forwarded-Proto': 'https'}
        for _ in range(5):
            self.login(headers=via_proxy('203.0.113.1'))
        self.assertEqual(self.login(headers=via_proxy('203.0.113.1')).status_code, 429)
        self.assertEqual(self.login(headers=via_proxy('203.0.113.2')).status_code, 400)


class LockoutTests(SecurityTestCase):
    overrides = {'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False}

    def normalized(self, response, email):
        # Nonces and CSRF tokens are random or timestamped on every response.
        text = re.sub(r'(nonce|name="csrf_token" value)="[^"]+"', '', response.text)
        text = text.replace(email, 'EMAIL')
        return response.status_code, text

    def test_ten_failures_lock_the_email(self):
        for _ in range(10):
            self.assertEqual(self.login().status_code, 400)
        with self.assertLogs(self.app.logger, 'WARNING') as logs:
            response = self.login(password=TEST_PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertIn('Email or password is incorrect. Too many attempts. Try again in a few minutes.',
                      response.text)
        self.assertEqual(self.client.get('/').status_code, 302)   # still logged out
        output = ' '.join(logs.output)
        self.assertIn('email locked', output)
        self.assertNotIn(TEST_EMAIL, output)
        # Other accounts are unaffected.
        add_user(self.path, 'other@example.com')
        self.assertEqual(self.login('other@example.com', TEST_PASSWORD).status_code, 302)

    def test_lockout_looks_the_same_for_unknown_emails(self):
        real, unknown = TEST_EMAIL, 'nobody@example.com'
        for attempt in range(12):
            with self.subTest(attempt=attempt):
                self.assertEqual(self.normalized(self.login(real), real),
                                 self.normalized(self.login(unknown), unknown))

    def test_success_resets_the_count(self):
        for _ in range(9):
            self.login()
        self.assertEqual(self.login(password=TEST_PASSWORD).status_code, 302)
        self.client.post('/logout')
        for _ in range(9):
            self.login()
        self.assertEqual(self.login(password=TEST_PASSWORD).status_code, 302)

    def test_lock_expires_after_fifteen_minutes(self):
        now = datetime(2026, 11, 18, 9, 0)
        with self.app.app_context():
            key = security.email_key(TEST_EMAIL)
        db = connect(self.path)
        self.addCleanup(db.close)
        for minute in range(9):
            self.assertFalse(security.record_failed_login(db, key, now + timedelta(minutes=minute)))
        self.assertTrue(security.record_failed_login(db, key, now + timedelta(minutes=9)))
        self.assertTrue(security.login_locked(db, key, now + timedelta(minutes=23)))
        self.assertFalse(security.login_locked(db, key, now + timedelta(minutes=25)))

    def test_failures_spread_out_do_not_lock(self):
        now = datetime(2026, 11, 18, 9, 0)
        with self.app.app_context():
            key = security.email_key(TEST_EMAIL)
        db = connect(self.path)
        self.addCleanup(db.close)
        for step in range(20):
            self.assertFalse(security.record_failed_login(db, key, now + timedelta(minutes=2 * step)))
        self.assertFalse(security.login_locked(db, key, now + timedelta(minutes=40)))

    def test_failed_login_log_has_ip_and_hash_only(self):
        with self.assertLogs(self.app.logger, 'WARNING') as logs:
            self.login(password='hunter2-secret')
        output = ' '.join(logs.output)
        self.assertIn('Failed login from 127.0.0.1', output)
        with self.app.app_context():
            self.assertIn(security.log_label(TEST_EMAIL), output)
        self.assertNotIn(TEST_EMAIL, output)
        self.assertNotIn('hunter2-secret', output)


class SessionTests(SecurityTestCase):
    overrides = {'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False}

    def test_login_starts_a_fresh_session(self):
        with self.client.session_transaction() as session:
            session['planted'] = 'value'
        self.login(password=TEST_PASSWORD)
        with self.client.session_transaction() as session:
            self.assertNotIn('planted', session)
            self.assertEqual(session['_user_id'], f'{self.user_id}:0')

    def test_logout_clears_the_session(self):
        self.login(password=TEST_PASSWORD)
        with self.client.session_transaction() as session:
            session['planted'] = 'value'
        self.client.post('/logout')
        with self.client.session_transaction() as session:
            self.assertNotIn('planted', session)
            self.assertNotIn('_user_id', session)
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_session_from_another_browser_is_dropped(self):
        self.login(password=TEST_PASSWORD)
        self.assertEqual(self.client.get('/').status_code, 200)
        response = self.client.get('/', headers={'User-Agent': 'Some other browser'})
        self.assertEqual(response.status_code, 302)

    def test_keep_me_logged_in_survives_more_than_one_request(self):
        response = self.client.post('/login', data={'email': TEST_EMAIL, 'password': TEST_PASSWORD,
                                                    'remember': '1'})
        remember = self.client.get_cookie('remember_token')
        self.assertIsNotNone(remember, response.headers.getlist('Set-Cookie'))
        browser = self.app.test_client()   # session cookie gone, as after closing the browser
        browser.set_cookie('remember_token', remember.value)
        for _ in range(3):
            self.assertEqual(browser.get('/').status_code, 200)


class HeaderTests(SecurityTestCase):
    overrides = {'WTF_CSRF_ENABLED': False, 'RATELIMIT_ENABLED': False}

    def setUp(self):
        super().setUp()
        db = connect(self.path)
        now = datetime.now().isoformat()
        entry_id = db.insert(
            'INSERT INTO entries (title, content, mood, created_at, user_id) VALUES (?, ?, ?, ?, ?)',
            ('Day', 'Text', 'Happy', now, self.user_id))
        trashed = db.insert(
            'INSERT INTO entries (title, content, mood, created_at, deleted_at, user_id) '
            'VALUES (?, ?, ?, ?, ?, ?)', ('Old', 'Text', 'Sad', now, now, self.user_id))
        db.commit()
        db.close()
        self.entry_id = entry_id
        self.trashed_id = trashed

    def check_headers(self, response, production=False):
        headers = response.headers
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(headers['Permissions-Policy'], 'camera=(), microphone=(), geolocation=()')
        csp = headers['Content-Security-Policy']
        nonce = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]{20,})'", csp).group(1)
        for part in ("default-src 'self'", "style-src 'self' 'unsafe-inline'",
                     "img-src 'self' data:", "font-src 'self'", "form-action 'self'",
                     "frame-ancestors 'none'", "base-uri 'self'", "object-src 'none'"):
            self.assertIn(part, csp)
        self.assertEqual('Strict-Transport-Security' in headers, production)
        return nonce

    def check_page(self, client, path, status=200):
        response = client.get(path)
        self.assertEqual(response.status_code, status, path)
        nonce = self.check_headers(response)
        scripts = re.findall(r'<script\b[^>]*>', response.text)
        self.assertTrue(scripts, path)
        for tag in scripts:
            self.assertIn(f'nonce="{nonce}"', tag, path)
        self.assertNotRegex(response.text, r'<[a-z][^>]*\son[a-z]+\s*=', path)
        return nonce

    def test_every_page_sets_the_headers_and_matching_nonce(self):
        logged_out = self.app.test_client()
        for path in ('/login', '/signup'):
            with self.subTest(path=path):
                self.check_page(logged_out, path)
        log_in_as(self.client, self.user_id)
        self.client.post(f'/delete/{self.entry_id}')   # a toast with Undo on the next page
        self.client.post(f'/restore/{self.entry_id}')
        for path in ('/', '/write', '/history', '/insights', '/week', '/backup', '/trash',
                     f'/entry/{self.entry_id}', f'/entry/{self.trashed_id}',
                     f'/edit/{self.entry_id}', f'/entry/{self.entry_id}?saved=1'):
            with self.subTest(path=path):
                self.check_page(self.client, path)
        with self.subTest(path='404'):
            self.check_page(self.client, '/no-such-page', 404)

    def test_toast_undo_form_has_a_token(self):
        log_in_as(self.client, self.user_id)
        response = self.client.post(f'/delete/{self.entry_id}', follow_redirects=True)
        self.assertIn('toast-action', response.text)
        toast = re.search(r'<form[^>]*toast-action.*?</form>', response.text, re.S).group(0)
        self.assertIn('name="csrf_token"', toast)

    def test_nonce_changes_every_request(self):
        nonces = {self.check_page(self.client, '/login') for _ in range(3)}
        self.assertEqual(len(nonces), 3)

    def test_static_files_and_errors_get_headers_too(self):
        self.check_headers(self.client.get('/static/style.css'))
        self.check_headers(self.client.post('/login', data={}))


class TemplateScanTests(unittest.TestCase):
    def templates(self):
        files = sorted(TEMPLATES.glob('*.html'))
        self.assertTrue(files)
        return [(path.name, path.read_text()) for path in files]

    def test_no_inline_event_handler_attributes(self):
        for name, text in self.templates():
            with self.subTest(template=name):
                self.assertNotRegex(text, r'<[a-zA-Z][^>]*\son[a-z]+\s*=')

    def test_every_script_tag_has_the_nonce(self):
        for name, text in self.templates():
            for tag in re.findall(r'<script\b[^>]*>', text):
                with self.subTest(template=name, tag=tag):
                    self.assertIn('nonce="{{ csp_nonce }}"', tag)

    def test_no_javascript_urls(self):
        for name, text in self.templates():
            with self.subTest(template=name):
                self.assertNotIn('javascript:', text.lower())


class ProductionConfigTests(unittest.TestCase):
    # Production needs a PostgreSQL URL. These tests never connect to it:
    # make_app points the app at a test database instead.
    PRODUCTION = {'APP_ENV': 'production', 'SECRET_KEY': SECRET,
                  'DATABASE_URL': 'postgresql://user:pw@db.example.com/journal'}

    def test_production_cookies_are_secure(self):
        settings = load_config(self.PRODUCTION)
        for name in ('SESSION_COOKIE_SECURE', 'REMEMBER_COOKIE_SECURE',
                     'SESSION_COOKIE_HTTPONLY', 'REMEMBER_COOKIE_HTTPONLY'):
            self.assertIs(settings[name], True, name)
        self.assertEqual(settings['SESSION_COOKIE_SAMESITE'], 'Lax')
        self.assertEqual(settings['REMEMBER_COOKIE_SAMESITE'], 'Lax')
        self.assertEqual(settings['REMEMBER_COOKIE_DURATION'], timedelta(days=30))
        self.assertEqual(settings['PREFERRED_URL_SCHEME'], 'https')
        self.assertIs(settings['DEBUG'], False)

    def test_production_ignores_flask_debug(self):
        self.assertIs(load_config({**self.PRODUCTION, 'FLASK_DEBUG': '1'})['DEBUG'], False)

    def test_development_cookies_work_over_http(self):
        settings = load_config({})
        self.assertIs(settings['SESSION_COOKIE_SECURE'], False)
        self.assertIs(settings['REMEMBER_COOKIE_SECURE'], False)
        self.assertIs(settings['SESSION_COOKIE_HTTPONLY'], True)
        self.assertEqual(settings['PREFERRED_URL_SCHEME'], 'http')

    def make_app(self, environ):
        import app as module
        with mock.patch.dict(os.environ, environ, clear=False):
            for name in ('APP_ENV', 'SECRET_KEY'):
                if name not in environ:
                    os.environ.pop(name, None)
            # The config is read from the environment; the data lives in a test database.
            application = module.create_app({'TESTING': True,
                                             'DATABASE': new_test_database(self)})
        return application

    def test_production_sends_hsts_and_secure_cookies(self):
        application = self.make_app(self.PRODUCTION)
        self.assertFalse(application.debug)
        response = application.test_client().get('/login', headers={'X-Forwarded-Proto': 'https'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Strict-Transport-Security'],
                         'max-age=31536000; includeSubDomains')
        self.assertIn('Secure', ' '.join(response.headers.getlist('Set-Cookie')))

    def test_development_has_no_hsts(self):
        application = self.make_app({'APP_ENV': 'development'})
        response = application.test_client().get('/login')
        self.assertNotIn('Strict-Transport-Security', response.headers)
        self.assertNotIn('Secure', ' '.join(response.headers.getlist('Set-Cookie')))


if __name__ == '__main__':
    unittest.main()
