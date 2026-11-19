"""The product name, Innerdesk, everywhere people see it; old keys kept."""
import importlib
from pathlib import Path
import re
import unittest

import config
import security
from tests.helpers import (SECURITY_OFF, TEST_EMAIL, add_user, log_in_as, new_test_database,
                           skip_daily_maintenance)

ROOT = Path(__file__).resolve().parent.parent


class BrandingTests(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module('app')
        self.path = new_test_database(self)
        self.outbox = []
        self.app = module.create_app({'TESTING': True, 'DATABASE': self.path,
                                      'MAIL_OUTBOX': self.outbox, **SECURITY_OFF})
        skip_daily_maintenance(module, self.path)
        security.limiter.reset()
        self.user_id = add_user(self.path, display_name='Ann Lee')
        self.client = self.app.test_client()

    def title(self, page):
        return re.search(r'<title>(.*?)</title>', page, re.S).group(1).strip()

    def test_name_and_tagline(self):
        self.assertEqual(config.APP_NAME, 'Innerdesk')
        self.assertEqual(config.APP_TAGLINE, 'A quiet place for your thoughts and your day.')

    def test_logged_out_pages(self):
        for url, title in [('/login', 'Log in | Innerdesk'), ('/signup', 'Create an account | Innerdesk'),
                           ('/forgot', 'Reset your password | Innerdesk')]:
            with self.subTest(url=url):
                page = self.client.get(url).text
                self.assertEqual(self.title(page), title)
                self.assertIn('</i> Innerdesk</p>', page)   # the heading's brand line
                self.assertIn('<meta name="description" content="A quiet place for your thoughts '
                              'and your day.">', page)
                self.assertNotIn('My Journal', page)

    def test_signed_in_pages(self):
        log_in_as(self.client, self.user_id)
        for url, title in [('/', 'Home | Innerdesk'), ('/history', 'History | Innerdesk'),
                           ('/write', 'Write | Innerdesk'), ('/insights', 'Insights | Innerdesk'),
                           ('/settings', 'Settings | Innerdesk'), ('/backup', 'Backup & export | Innerdesk'),
                           ('/no-such-page', 'Page not found | Innerdesk')]:
            with self.subTest(url=url):
                page = self.client.get(url).text
                self.assertEqual(self.title(page), title)
                self.assertIn('<span>Innerdesk</span>', page)   # sidebar brand
                self.assertNotIn('My Journal', page)

    def test_favicon_is_an_inline_svg(self):
        page = self.client.get('/login').text
        icon = re.search(r'<link rel="icon"[^>]*>', page).group(0)
        self.assertIn('href="data:image/svg+xml,', icon)
        self.assertIn('rx=%2214%22', icon)                  # rounded square
        self.assertIn('fill=%22%232c4a8c%22', icon)         # the accent color
        self.assertIn('fill=%22white%22', icon)             # the white "i"
        self.assertNotIn('📓', page)

    def test_reset_email(self):
        self.client.post('/forgot', data={'email': TEST_EMAIL})
        mail = self.outbox[0]
        self.assertEqual(mail['subject'], 'Reset your Innerdesk password')
        self.assertIn('your Innerdesk account', mail['body'])
        self.assertTrue(mail['body'].rstrip().endswith('Innerdesk'))
        self.assertNotIn('My Journal', mail['body'])

    def test_browser_storage_keys_are_unchanged(self):
        # Renaming them would lose drafts and settings already in browsers.
        log_in_as(self.client, self.user_id)
        write = self.client.get('/write').text
        self.assertIn(f'data-draft-key="my-journal-new-entry-draft-{self.user_id}"', write)
        self.assertIn('"my-journal-save-pending"', write)
        self.assertIn('"my-journal-focus-mode"', write)
        self.assertIn("'my-journal-focus-mode'", (ROOT / 'static' / 'write-extras.js').read_text())


if __name__ == '__main__':
    unittest.main()
