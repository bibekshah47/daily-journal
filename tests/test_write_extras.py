"""Writing templates and focus mode on the Write and Edit pages, using temporary databases only."""
from datetime import date
import hashlib
import html
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from database import connect, initialize_database
from rich_text import clean_html
from templates_data import WRITING_TEMPLATES
from tests.helpers import signed_in_client


class TemplateDataTests(unittest.TestCase):
    def test_four_templates_with_unique_keys(self):
        self.assertEqual([t['name'] for t in WRITING_TEMPLATES],
                         ['Gratitude', 'Morning pages', 'Brain dump', 'Evening check-in'])
        self.assertEqual(len({t['key'] for t in WRITING_TEMPLATES}), 4)
        for template in WRITING_TEMPLATES:
            self.assertEqual(set(template), {'key', 'name', 'html'})

    def test_html_survives_the_sanitizer_unchanged(self):
        for template in WRITING_TEMPLATES:
            with self.subTest(template=template['key']):
                self.assertEqual(clean_html(template['html']), template['html'])


class WriteExtrasPageTests(unittest.TestCase):
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
        self.client, self.user_id = signed_in_client(self.app, self.path)
        # Skip the once-a-day backup/cleanup hook.
        self.module._daily_backups_checked.add((os.path.abspath(self.path), date.today()))

    def tearDown(self):
        self.tmp.cleanup()

    def add_entry(self):
        db = connect(self.path)
        try:
            cursor = db.execute('INSERT INTO entries (content, created_at, user_id) VALUES (?, ?, ?)',
                                ('Existing words', '2026-11-16T09:00:00', self.user_id))
            db.commit()
            return cursor.lastrowid
        finally:
            db.close()

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_write_page_has_the_template_line_and_json(self):
        page = self.page('/write')
        self.assertIn('Start from:', page)
        for template in WRITING_TEMPLATES:
            self.assertIn(f'data-template="{template["key"]}">{html.escape(template["name"])}</button>', page)
        block = re.search(r'<script type="application/json" id="writing-templates">(.*?)</script>', page, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(json.loads(block.group(1)), WRITING_TEMPLATES)
        # tojson escapes "<", so the HTML can't close the script element early.
        self.assertNotIn('<h3>', block.group(1))
        self.assertIn('id="template-dialog"', page)
        self.assertIn('write-extras.js', page)

    def test_write_page_after_validation_error_keeps_templates(self):
        response = self.client.post('/write', data={'journal': '   '})
        self.assertEqual(response.status_code, 400)
        self.assertIn('id="writing-templates"', response.get_data(as_text=True))

    def test_edit_page_has_no_template_line(self):
        page = self.page(f'/edit/{self.add_entry()}')
        self.assertNotIn('Start from:', page)
        self.assertNotIn('id="template-picker"', page)
        self.assertNotIn('id="writing-templates"', page)
        self.assertIn('write-extras.js', page)

    def test_both_pages_have_the_focus_button(self):
        for url in ('/write', f'/edit/{self.add_entry()}'):
            with self.subTest(url=url):
                page = self.page(url)
                self.assertIn('id="focus-toggle"', page)
                self.assertIn('aria-pressed="false"', page)
                self.assertIn('data-lucide="maximize-2"', page)
                self.assertIn('data-lucide="minimize-2"', page)
                self.assertIn('my-journal-focus-mode', page)

    def test_other_pages_have_no_focus_button(self):
        self.assertNotIn('id="focus-toggle"', self.page('/'))


if __name__ == '__main__':
    unittest.main()
