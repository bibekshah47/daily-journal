"""Run with: python -m unittest discover -s tests -v (temporary databases only)."""
import hashlib
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from database import connect, initialize_database
from tests.helpers import (SECURITY_OFF, TEST_EMAIL, add_user, log_in_as, new_test_database,
                           signed_in_client, sqlite_only)
from rich_text import clean_html, prepare_content


class RichTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.real = Path('journal.db')
        cls.digest = hashlib.sha256(cls.real.read_bytes()).hexdigest() if cls.real.exists() else None
        cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        if cls.digest:
            assert hashlib.sha256(cls.real.read_bytes()).hexdigest() == cls.digest
        cls.tmp.cleanup()

    def setUp(self):
        self.path = new_test_database(self)
        self.module.app.config.update(TESTING=True, **SECURITY_OFF, DATABASE=self.path)
        self.client, self.user_id = signed_in_client(self.module.app, self.path)

    def row(self, entry_id):
        with connect(self.path) as db:
            return dict(db.execute('SELECT * FROM entries WHERE id=?', (entry_id,)).fetchone())

    def create(self, **fields):
        response = self.client.post('/write', data={'journal': 'Plain body', **fields})
        self.assertEqual(response.status_code, 302)
        return int(response.location.split('/entry/')[1].split('?')[0])

    @sqlite_only
    def test_explicit_repeatable_migration_preserves_legacy(self):
        legacy = str(Path(self.tmp.name) / 'legacy.db')
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE entries (id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL, mood TEXT, title TEXT, is_favorite INTEGER NOT NULL DEFAULT 0)')
            db.execute("INSERT INTO entries VALUES (8, '<b>literal</b>\nnext', '2026-11-13', 'Happy', 'Old', 1)")
        initialize_database(legacy)
        initialize_database(legacy)
        with sqlite3.connect(legacy) as db:
            row = db.execute('SELECT content,content_html,is_favorite FROM entries WHERE id=8').fetchone()
        self.assertEqual(row, ('<b>literal</b>\nnext', None, 1))

    @sqlite_only
    def test_startup_migrates_before_save_and_preserves_all_existing_fields(self):
        legacy = str(Path(self.tmp.name) / 'request-legacy.db')
        original = (7, 'Existing body', '2026-11-13T09:00:00', 'Happy',
                    'Existing title', 1, 'Win', 'Challenge', 'Plan', 'Custom value')
        with sqlite3.connect(legacy) as db:
            db.execute("""CREATE TABLE entries (
                id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL,
                mood TEXT, title TEXT, is_favorite INTEGER NOT NULL DEFAULT 0,
                went_well TEXT, went_wrong TEXT, improve_tomorrow TEXT, custom_column TEXT
            )""")
            db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)', original)
        # No manual initializer: starting the app migrates the file (adding
        # the users table) before any request reads or writes.
        app = self.module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': legacy})
        client = app.test_client()
        self.assertEqual(client.get('/login').status_code, 200)
        log_in_as(client, add_user(legacy))
        response = client.post('/write', data={
            'editor_format': 'html', 'journal_html': '<p><strong>New</strong></p>'})
        self.assertEqual(response.status_code, 302)
        # Entry 7 is from before accounts: nobody's until it is claimed.
        self.assertEqual(client.get('/entry/7').status_code, 404)
        result = app.test_cli_runner().invoke(args=['assign-entries', '--email', TEST_EMAIL])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Assigned 1 entry and 0 tags', result.output)
        self.client = client
        self.assertEqual(self.client.get('/entry/7').status_code, 200)
        self.assertEqual(self.client.get('/edit/7').status_code, 200)
        self.assertEqual(self.client.get('/history?favorites=1').status_code, 200)
        with sqlite3.connect(legacy) as db:
            columns = {row[1]: row[2] for row in db.execute('PRAGMA table_info(entries)')}
            self.assertEqual(columns['content_html'], 'TEXT')
            preserved = db.execute("""SELECT id,content,created_at,mood,title,is_favorite,
                went_well,went_wrong,improve_tomorrow,custom_column
                FROM entries WHERE id=7""").fetchone()
            self.assertEqual(db.execute('SELECT user_id FROM entries WHERE id=7').fetchone()[0], 1)
            self.assertEqual(preserved, original)
            self.assertIsNone(db.execute('SELECT content_html FROM entries WHERE id=7').fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 2)
        # CLI reuses the same migration and is safe after repeated requests.
        result = app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, result.output)

    def test_legacy_literal_text(self):
        entry_id = self.create(journal='<b>literal</b>\nnext')
        page = self.client.get(f'/entry/{entry_id}').text
        self.assertIn('&lt;b&gt;literal&lt;/b&gt;\nnext', page)
        self.assertIsNone(self.row(entry_id)['content_html'])

    def test_rich_roundtrip_search_and_edit(self):
        entry_id = self.create(editor_format='html', journal_html='<h2>Title</h2><p><strong>bold searchable</strong></p><ul><li>First</li></ul>', went_well='A win')
        row = self.row(entry_id)
        self.assertEqual(row['content'], 'Title\nbold searchable\nFirst')
        self.assertIn('<strong>bold searchable</strong>', self.client.get(f'/entry/{entry_id}').text)
        self.assertIn('<strong>bold searchable</strong>', self.client.get(f'/edit/{entry_id}').text)
        self.assertIn('bold searchable', self.client.get('/history?search=searchable').text)
        response = self.client.post(f'/edit/{entry_id}', data={'editor_format':'html', 'journal_html':'<p><em>Updated</em></p>', 'went_well':'Kept'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.row(entry_id)['content'], 'Updated')
        self.assertEqual(self.row(entry_id)['went_well'], 'Kept')
        self.assertIn('<em>Updated</em>', self.row(entry_id)['content_html'])

    def test_empty_html_and_validation_preservation(self):
        for html in ['<p><br></p>', '<p>&nbsp;</p>', '<script>alert(1)</script>', '<img src=x>']:
            self.assertEqual(self.client.post('/write', data={'editor_format':'html', 'journal_html':html}).status_code,400)
        entry_id = self.create()
        response = self.client.post(f'/edit/{entry_id}', data={'editor_format':'html','journal_html':'<p><br></p>','went_well':'Still here'})
        self.assertEqual(response.status_code,400)
        self.assertIn('Still here',response.text)
        self.assertEqual(self.row(entry_id)['content'],'Plain body')

    def test_sanitization_on_save_and_display(self):
        dangerous = '<p onclick="bad()" style="color:red">Safe<script>alert(1)</script><img src=x onerror=bad()><a href="javascript:bad()">link</a></p>'
        entry_id = self.create(editor_format='html', journal_html=dangerous)
        html = self.row(entry_id)['content_html']
        for bad in ['onclick','onerror','javascript:','<script','<img','style=']:
            self.assertNotIn(bad, html)
        with connect(self.path) as db:
            db.execute('UPDATE entries SET content_html=? WHERE id=?',(dangerous,entry_id))
        page = self.client.get(f'/entry/{entry_id}').text
        self.assertNotIn('onclick=',page)
        self.assertNotIn('javascript:',page)
        self.assertIn('<a rel="noopener noreferrer">link</a>',page)
        self.assertIn('href="https://example.com"', clean_html('<a href="https://example.com">Safe link</a>'))

    def test_highlight_save_reopen_edit_and_color_allowlist(self):
        from rich_text import HIGHLIGHT_COLORS
        for color in HIGHLIGHT_COLORS:
            rgb = ', '.join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5))
            html = f'<p><span style="background-color: rgb({rgb});"><strong>Highlighted</strong></span></p>'
            entry_id = self.create(editor_format='html', journal_html=html)
            expected = f'style="background-color: {color};"'
            self.assertIn(expected, self.row(entry_id)['content_html'])
            self.assertEqual(self.row(entry_id)['content'], 'Highlighted')
            self.assertIn(expected, self.client.get(f'/entry/{entry_id}').text)
            self.assertIn(expected, self.client.get(f'/edit/{entry_id}').text)
            response = self.client.post(f'/edit/{entry_id}', data={
                'editor_format': 'html', 'journal_html': self.row(entry_id)['content_html'],
                'went_well': 'Win'})
            self.assertEqual(response.status_code, 302)
            self.assertIn(expected, self.client.get(response.location).text)
            self.assertEqual(self.row(entry_id)['went_well'], 'Win')
            self.assertEqual(clean_html(clean_html(html)), clean_html(html))
        bad_styles = [
            'background-color: red', 'background-color: #ff0000',
            'background: url(javascript:alert(1))',
            'background-color: #fff2b2; position: fixed',
            'background-color: var(--unsafe)', 'background-color: #fff2b2 !important',
            'color: red', 'background-image: url(https://example.com/tracker)',
        ]
        for style in bad_styles:
            cleaned = clean_html(f'<span style="{style}" onclick="bad()">Text</span>')
            self.assertEqual(cleaned, '<span>Text</span>')
        # Even approved style values are not allowed on arbitrary elements.
        self.assertEqual(clean_html('<p style="background-color: #fff2b2;">Text</p>'), '<p>Text</p>')

    def test_existing_workflows_and_fallback(self):
        entry_id = self.create(title='Find me',mood='Happy',went_wrong='Challenge',improve_tomorrow='Plan')
        for url in ['/','/history','/write',f'/edit/{entry_id}']:
            self.assertEqual(self.client.get(url).status_code,200)
        response = self.client.post(f'/favorite/{entry_id}',data={'search':'Find','favorites':'1'})
        self.assertIn('search=Find',response.location)
        self.assertIn('Find me',self.client.get('/history?favorites=1').text)
        self.assertEqual(self.client.post(f'/delete/{entry_id}').status_code,302)
        # Deleting moves the entry to Trash; erasing it from there makes it 404.
        self.assertIn('This entry is in Trash',self.client.get(f'/entry/{entry_id}').text)
        self.client.post(f'/trash/{entry_id}/purge')
        self.assertEqual(self.client.get(f'/entry/{entry_id}').status_code,404)
        self.assertEqual(prepare_content({'journal':'literal <b>text</b>'}),('literal <b>text</b>',None))


if __name__ == '__main__':
    unittest.main()
