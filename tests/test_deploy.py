"""Deploying on Render: health check, HTTPS, trusted hosts, logging, start files."""
from datetime import timedelta
import importlib
import io
import logging
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import logs
import security
from tests.helpers import SECURITY_OFF, add_user, new_test_database, skip_daily_maintenance

ROOT = Path(__file__).resolve().parent.parent
SECRET = 'deploy-test-secret-' + 'z' * 40
HTTPS = {'X-Forwarded-Proto': 'https'}   # as Render's proxy sends it
# Render's proxy talks to the app over plain HTTP. (Flask's test client would
# otherwise use the production PREFERRED_URL_SCHEME, https.)
PLAIN = 'http://localhost'


class DeployTestCase(unittest.TestCase):
    def make_app(self, production=False, trusted_hosts='', mail_server='', **overrides):
        """An app whose settings come from the environment, like on Render, with
        its data in a test database."""
        module = importlib.import_module('app')
        environ = {'APP_ENV': 'production' if production else 'development',
                   'SECRET_KEY': SECRET, 'TRUSTED_HOSTS': trusted_hosts, 'MAIL_SERVER': mail_server,
                   'DATABASE_URL': 'postgresql://user:pw@db.example.com/journal' if production else ''}
        target = new_test_database(self)
        with mock.patch.dict(os.environ, environ):
            app = module.create_app({'TESTING': True, 'DATABASE': target, **overrides})
        skip_daily_maintenance(module, target)
        security.limiter.reset()
        self.target = target
        return app


class HealthCheckTests(DeployTestCase):
    def test_ok_logged_out_without_cookies(self):
        app = self.make_app()   # CSRF and rate limits on
        client = app.test_client()
        response = client.get('/healthz')
        self.assertEqual((response.status_code, response.text), (200, 'ok'))
        self.assertEqual(response.mimetype, 'text/plain')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')   # still gets the headers
        self.assertEqual(client.head('/healthz').status_code, 200)

    def test_never_rate_limited(self):
        client = self.make_app().test_client()
        for _ in range(40):
            self.assertEqual(client.get('/healthz').status_code, 200)

    def test_does_not_run_the_daily_maintenance(self):
        module = importlib.import_module('app')
        app = self.make_app()
        module._daily_backups_checked.clear()
        app.test_client().get('/healthz')
        self.assertNotIn(module.maintenance_key(self.target), module._daily_backups_checked)

    def test_503_when_the_database_is_down(self):
        app = self.make_app()
        app.config['DATABASE'] = str(ROOT / 'no-such-folder' / 'secret-name.db')
        with self.assertLogs(app.logger, 'ERROR') as captured:
            response = app.test_client().get('/healthz')
        self.assertEqual((response.status_code, response.text), (503, 'database unavailable'))
        self.assertIn('Health check failed', captured.output[0])
        self.assertNotIn('secret-name', ' '.join(captured.output))   # no connection details
        # Everything else still needs a login.
        self.assertEqual(app.test_client().get('/').status_code, 302)

    def test_works_in_production_over_http_and_for_any_host(self):
        app = self.make_app(production=True, trusted_hosts='journal.example.com')
        for headers in ({}, HTTPS, {'Host': '10.0.0.7:10000'}):
            with self.subTest(headers=headers):
                response = app.test_client().get('/healthz', base_url=PLAIN, headers=headers)
                self.assertEqual(response.status_code, 200)


class HttpsTests(DeployTestCase):
    def test_production_redirects_http_to_https(self):
        client = self.make_app(production=True).test_client()
        response = client.get('/login?next=/history', base_url=PLAIN)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.location, 'https://localhost/login?next=/history')
        post = client.post('/login', base_url=PLAIN, data={'email': 'a@example.com'})
        self.assertEqual(post.status_code, 308)   # keeps the method and the form
        self.assertEqual(post.location, 'https://localhost/login')

    def test_production_behind_the_proxy_is_https(self):
        app = self.make_app(production=True, MAIL_OUTBOX=[], **SECURITY_OFF)
        add_user(self.target)
        client = app.test_client()
        page = client.get('/login', base_url=PLAIN, headers=HTTPS)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Secure', ' '.join(page.headers.getlist('Set-Cookie')))
        # Links in emails are https.
        client.post('/forgot', base_url=PLAIN, data={'email': 'me@example.com'}, headers=HTTPS)
        self.assertIn('https://localhost/reset/', app.config['MAIL_OUTBOX'][0]['body'])
        # Through the real middleware: the proxy's header is what makes it HTTPS.
        from flask import request, url_for
        with client:
            client.get('/login', base_url=PLAIN, headers=HTTPS)
            self.assertTrue(request.is_secure)
            self.assertTrue(url_for('auth.login', _external=True).startswith('https://'))
        with client:
            self.assertEqual(client.get('/login', base_url=PLAIN).status_code, 301)
            self.assertFalse(request.is_secure)

    def test_development_does_not_redirect(self):
        client = self.make_app().test_client()
        self.assertEqual(client.get('/login', base_url=PLAIN).status_code, 200)


class TrustedHostTests(DeployTestCase):
    HOSTS = 'journal.example.com, www.journal.example.com'

    def test_production_refuses_other_hosts(self):
        client = self.make_app(production=True, trusted_hosts=self.HOSTS).test_client()
        for host in ('journal.example.com', 'www.journal.example.com', 'journal.example.com:443'):
            with self.subTest(host=host):
                self.assertEqual(client.get('/login', headers={**HTTPS, 'Host': host}).status_code, 200)
        for host in ('evil.example', 'journal.example.com.evil.example', 'example.com'):
            with self.subTest(host=host):
                response = client.get('/login', headers={**HTTPS, 'Host': host})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Set-Cookie', response.headers)
        # Checked before the HTTPS redirect, so it never points at a forged host.
        response = client.get('/login', base_url=PLAIN, headers={'Host': 'evil.example'})
        self.assertEqual(response.status_code, 400)

    def test_production_without_trusted_hosts_accepts_any_host(self):
        client = self.make_app(production=True).test_client()
        self.assertEqual(client.get('/login', headers={**HTTPS, 'Host': 'anything.example'}).status_code, 200)

    def test_development_ignores_trusted_hosts(self):
        client = self.make_app(trusted_hosts=self.HOSTS).test_client()
        self.assertEqual(client.get('/login', headers={'Host': 'evil.example'}).status_code, 200)


class LoggingTests(DeployTestCase):
    def test_production_logs_info_to_stdout_once(self):
        app = self.make_app(production=True, mail_server='smtp.example.com')
        self.make_app(production=True, mail_server='smtp.example.com')
        handlers = [h for h in app.logger.handlers if isinstance(h, logs._StdoutHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertIs(handlers[0].stream, sys.stdout)
        self.assertEqual(app.logger.level, logging.INFO)

    def test_warns_at_startup_without_mail_server(self):
        with self.assertLogs('app', 'WARNING') as captured:
            self.make_app(production=True)
        self.assertIn("password reset emails can't be sent", ' '.join(captured.output))

    def test_no_mail_warning_when_configured_or_in_development(self):
        for kwargs in ({'production': True, 'mail_server': 'smtp.example.com'}, {}):
            with self.subTest(**kwargs):
                with self.assertNoLogs('app', 'WARNING'):
                    self.make_app(**kwargs)

    def test_reset_tokens_are_redacted(self):
        line = 'GET /reset/eyJ1c2VyX2lkIjoxfQ.aBc.dEf-g_h?x=1 HTTP/1.1'
        self.assertEqual(logs.redact(line), 'GET /reset/[redacted]?x=1 HTTP/1.1')
        self.assertEqual(logs.redact('https://j.example.com/reset/abc'),
                         'https://j.example.com/reset/[redacted]')
        self.assertEqual(logs.redact('/history?search=reset'), '/history?search=reset')
        record = logging.LogRecord('app', logging.INFO, __file__, 1, 'Opened %s', ('/reset/tok123',), None)
        logs.RedactFilter().filter(record)
        self.assertEqual(record.getMessage(), 'Opened /reset/[redacted]')

    def test_app_log_lines_are_redacted_in_production(self):
        app = self.make_app(production=True, mail_server='smtp.example.com')
        handler = next(h for h in app.logger.handlers if isinstance(h, logs._StdoutHandler))
        stream = io.StringIO()
        with mock.patch.object(handler, 'stream', stream):
            app.logger.info('Visited %s', '/reset/super-secret-token')
        self.assertIn('/reset/[redacted]', stream.getvalue())
        self.assertNotIn('super-secret-token', stream.getvalue())

    def test_gunicorn_access_log_redacts_tokens(self):
        from gunicorn.config import Config
        settings = runpy.run_path(str(ROOT / 'gunicorn.conf.py'))
        logger = settings['logger_class'](Config())
        environ = {'REQUEST_METHOD': 'GET', 'RAW_URI': '/reset/abc.def-ghi', 'PATH_INFO': '/reset/abc.def-ghi',
                   'QUERY_STRING': '', 'SERVER_PROTOCOL': 'HTTP/1.1', 'REMOTE_ADDR': '203.0.113.9',
                   'HTTP_REFERER': 'https://j.example.com/reset/abc.def-ghi', 'HTTP_USER_AGENT': 'test'}
        response = SimpleNamespace(status='200 OK', headers=[], sent=10)
        request = SimpleNamespace(headers=[('Referer', 'https://j.example.com/reset/abc.def-ghi')])
        atoms = logger.atoms(response, request, environ, timedelta(milliseconds=5))
        line = logger.cfg.access_log_format % atoms
        self.assertNotIn('abc.def-ghi', line)
        self.assertNotIn('abc.def-ghi', str(atoms))
        self.assertIn('GET /reset/[redacted] HTTP/1.1', line)


class StartFilesTests(unittest.TestCase):
    def test_start_script(self):
        script = ROOT / 'start.sh'
        data = script.read_bytes()
        self.assertTrue(os.access(script, os.X_OK), 'start.sh must be executable')
        self.assertNotIn(b'\r', data, 'start.sh must have LF line endings')
        text = data.decode()
        self.assertTrue(text.startswith('#!'))
        lines = [line for line in text.splitlines() if line and not line.startswith('#')]
        self.assertIn('flask --app app init-db', lines)
        self.assertLess(lines.index('flask --app app init-db'),
                        next(i for i, line in enumerate(lines) if line.startswith('exec gunicorn')))
        self.assertIn('exec gunicorn "app:app" --bind 0.0.0.0:${PORT:-10000} --workers 1 '
                      '--threads 4 --timeout 60 --access-logfile -', lines)

    def test_python_version_and_requirements(self):
        version = (ROOT / '.python-version').read_text().strip()
        major, minor = (int(part) for part in version.split('.')[:2])
        self.assertGreaterEqual((major, minor), (3, 12))
        self.assertEqual((major, minor), sys.version_info[:2])   # what the project uses
        requirements = (ROOT / 'requirements.txt').read_text().lower()
        for package in ('gunicorn==', 'tzdata==', 'psycopg[binary]=='):
            self.assertIn(package, requirements)

    def test_init_db_is_safe_on_every_deploy(self):
        module = importlib.import_module('app')
        target = new_test_database(self)
        app = module.create_app({'TESTING': True, 'DATABASE': target})
        for _ in range(3):
            result = app.test_cli_runner().invoke(args=['init-db'])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn('up to date', result.output)


if __name__ == '__main__':
    unittest.main()
