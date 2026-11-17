"""Backup, export and restore, using temporary databases only."""
from datetime import date, timedelta
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from database import initialize_database
import backup


ENTRIES = [
    # title, content, mood, created_at, content_html, favorite, went_well, went_wrong, improve
    ('Morning pages', 'Coffee & a long walk', 'Happy', '2026-11-01T08:15:00',
     '<p>Coffee &amp; a <strong>long</strong> walk</p>', 1, 'Got outside', None, 'Sleep earlier'),
    ('', 'Untitled thoughts <not html>', None, '2026-11-03T21:40:00', None, 0, None, None, None),
    ('Late night', 'Finished the draft', 'Stressed', '2026-11-05T23:05:00',
     '<p>Finished the draft</p>', 0, None, 'Too many tabs', None),
]


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = Path('journal.db')
        cls.digest = hashlib.sha256(cls.real.read_bytes()).hexdigest() if cls.real.exists() else None
        cls.old_env = os.environ.get('JOURNAL_DATABASE')
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
        self.path = self.new_database('journal.db', ENTRIES)
        self.use_database(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    # ---------- helpers ----------

    def new_database(self, name, rows=()):
        folder = Path(self.tmp.name) / name.replace('.db', '')
        folder.mkdir()
        path = str(folder / name)
        initialize_database(path)
        with sqlite3.connect(path) as db:
            db.executemany(
                'INSERT INTO entries (title, content, mood, created_at, content_html, '
                'is_favorite, went_well, went_wrong, improve_tomorrow) VALUES (?,?,?,?,?,?,?,?,?)',
                rows)
        db.close()
        return path

    def use_database(self, path):
        self.app.config.update(TESTING=True, DATABASE=path)
        self.client = self.app.test_client()

    def rows(self, path):
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            result = [dict(row) for row in db.execute('SELECT * FROM entries ORDER BY id')]
        db.close()
        return result

    def restore(self, data, filename='backup.json'):
        return self.client.post('/backup/restore', data={'backup_file': (io.BytesIO(data), filename)},
                                content_type='multipart/form-data', follow_redirects=True)

    # ---------- page and exports ----------

    def test_page_shows_summary_and_sidebar_link(self):
        page = self.client.get('/backup')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Backup &amp; export', page.text)
        self.assertIn('Nov 1, 2026', page.text)   # oldest
        self.assertIn('Nov 5, 2026', page.text)   # newest
        self.assertIn('aria-current="page"', page.text)
        for url in ['/', '/history', '/insights']:
            self.assertIn('href="/backup"', self.client.get(url).text)

    def test_markdown_export(self):
        response = self.client.get('/backup/export.md')
        self.assertEqual(response.status_code, 200)
        today = date.today().isoformat()
        self.assertEqual(response.headers['Content-Disposition'],
                         f'attachment; filename=my-journal-{today}.md')
        text = response.get_data(as_text=True)
        self.assertIn('## Morning pages', text)
        self.assertIn('## November 3, 2026', text)   # untitled -> date heading
        self.assertIn('Mood: Happy', text)
        self.assertIn('Coffee & a long walk', text)
        self.assertIn('Untitled thoughts <not html>', text)
        self.assertIn("**What didn't go well today?**\nToo many tabs", text)
        self.assertIn('**What can I improve tomorrow?**\nSleep earlier', text)
        self.assertNotIn('<strong>', text)            # plain text, not HTML
        # Newest first, separated by horizontal rules.
        self.assertLess(text.index('## Late night'), text.index('## Morning pages'))
        self.assertEqual(text.count('\n---\n'), len(ENTRIES))

    def test_json_export_has_every_column(self):
        response = self.client.get('/backup/export.json')
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'my-journal-{date.today().isoformat()}.json',
                      response.headers['Content-Disposition'])
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment'))
        payload = response.get_json()
        self.assertEqual(payload['app'], 'my-journal')
        self.assertEqual(payload['version'], backup.EXPORT_VERSION)
        self.assertEqual(backup.EXPORT_VERSION, 2)
        self.assertIn('exported_at', payload)
        self.assertEqual(len(payload['entries']), 3)
        self.assertEqual(payload['entries'][0]['title'], 'Late night')
        db = sqlite3.connect(self.path)
        columns = {row[1] for row in db.execute('PRAGMA table_info(entries)')}
        db.close()
        for entry in payload['entries']:
            self.assertEqual(set(entry), columns | {'tags'})

    def test_database_export_is_an_exact_copy(self):
        response = self.client.get('/backup/export.db')
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'my-journal-{date.today().isoformat()}.db',
                      response.headers['Content-Disposition'])
        copy = Path(self.tmp.name) / 'downloaded.db'
        copy.write_bytes(response.data)
        self.assertEqual(self.rows(str(copy)), self.rows(self.path))

    # ---------- restore ----------

    def test_restore_into_empty_database_then_again_without_duplicates(self):
        exported = self.client.get('/backup/export.json').data
        original = self.rows(self.path)

        empty = self.new_database('empty.db')
        self.use_database(empty)
        page = self.restore(exported)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Restored 3 entries.', page.text)
        restored = self.rows(empty)
        strip_id = lambda rows: [{k: v for k, v in row.items() if k != 'id'} for row in rows]
        self.assertEqual(strip_id(restored), strip_id(original))

        page = self.restore(exported)
        self.assertIn('Nothing new to restore. All 3 entries were already here.', page.text)
        self.assertEqual(len(self.rows(empty)), 3)

    def test_restore_adds_only_new_entries_and_never_changes_existing(self):
        payload = json.loads(self.client.get('/backup/export.json').data)
        payload['entries'][0]['title'] = 'Renamed in the backup'   # same created_at + content
        payload['entries'].append(dict(payload['entries'][1], content='A new entry', id=999))
        before = self.rows(self.path)
        page = self.restore(json.dumps(payload).encode())
        self.assertIn('Restored 1 entry. Skipped 3 that were already here.', page.text)
        after = self.rows(self.path)
        self.assertEqual(after[:3], before)
        self.assertEqual(after[3]['content'], 'A new entry')
        self.assertNotEqual(after[3]['id'], 999)

    def test_restore_sanitizes_html(self):
        payload = {'app': 'my-journal', 'version': 1, 'entries': [{
            'content': 'Hi', 'created_at': '2026-11-10T10:00:00',
            'content_html': '<p onclick="x()">Hi<script>alert(1)</script></p>'}]}
        self.restore(json.dumps(payload).encode())
        stored = [row for row in self.rows(self.path) if row['content'] == 'Hi'][0]
        self.assertEqual(stored['content_html'], '<p>Hi</p>')

    def test_invalid_files_are_rejected_with_a_clear_error(self):
        before = self.rows(self.path)
        cases = [
            (b'{"app": "my-journal"', 'backup.json', "isn't valid JSON"),
            (b'{"app": "other-app", "version": 1, "entries": []}', 'backup.json',
             "isn't a My Journal backup"),
            (b'{"app": "my-journal", "version": 3, "entries": []}', 'backup.json',
             'can only restore versions 1 to 2'),
            (b'{"app": "my-journal", "version": 2, "entries": [{"content": "x", '
             b'"created_at": "2026-11-10T10:00:00", "tags": "work"}]}',
             'backup.json', 'Entry 1 has an invalid list of tags'),
            (b'{"app": "my-journal", "version": 1, "entries": [{"content": "x"}]}',
             'backup.json', 'Entry 1 is missing its text or date'),
            (b'# My Journal', 'export.md', 'Only .json backups can be restored'),
        ]
        for data, filename, message in cases:
            with self.subTest(message=message):
                response = self.restore(data, filename)
                self.assertEqual(response.status_code, 400)
                self.assertIn('role="alert"', response.text)
                self.assertIn(message, response.text.replace('&#39;', "'"))
        response = self.client.post('/backup/restore', data={}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Choose a .json backup file', response.text)
        self.assertEqual(self.rows(self.path), before)

    def test_upload_limit(self):
        self.assertEqual(self.app.config['MAX_CONTENT_LENGTH'], 10 * 1024 * 1024)
        response = self.restore(b' ' * (10 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertIn('larger than 10 MB', response.text)

    # ---------- automatic daily backups ----------

    def test_daily_backup_created_once_and_pruned_to_14(self):
        folder = backup.backup_folder(self.path)
        self.assertEqual(folder, Path(self.path).resolve().parent / 'backups')
        today = date(2026, 11, 14)

        created = backup.run_daily_backup(self.path, today)
        self.assertEqual(created.name, 'journal-2026-11-14.db')
        self.assertEqual(self.rows(str(created)), self.rows(self.path))
        stamp = created.stat().st_mtime_ns
        self.assertIsNone(backup.run_daily_backup(self.path, today))
        self.assertEqual(created.stat().st_mtime_ns, stamp)

        for offset in range(1, 21):
            backup.copy_database(self.path, folder / f'journal-{(today - timedelta(days=offset)).isoformat()}.db')
        (folder / 'notes.txt').write_text('not a backup')
        backup.run_daily_backup(self.path, today)
        names = [path.name for path in backup.list_backups(self.path)]
        self.assertEqual(len(names), 14)
        self.assertEqual(names[-1], 'journal-2026-11-14.db')
        self.assertEqual(names[0], 'journal-2026-11-01.db')
        self.assertTrue((folder / 'notes.txt').exists())

    def test_first_request_makes_backup_and_failures_never_break_pages(self):
        self.module._daily_backups_checked.clear()
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(len(backup.list_backups(self.path)), 1)
        self.assertEqual(self.client.get('/history').status_code, 200)
        self.assertEqual(len(backup.list_backups(self.path)), 1)   # still just one
        self.assertNotIn('Not yet', self.client.get('/backup').text)

        other = self.new_database('broken.db', ENTRIES)
        self.use_database(other)
        with mock.patch.object(self.module, 'run_daily_backup', side_effect=OSError('disk full')), \
                self.assertLogs(self.app.logger, 'ERROR') as logs:
            self.assertEqual(self.client.get('/history').status_code, 200)
        self.assertIn('Automatic daily backup failed', logs.output[0])
        self.assertEqual(backup.list_backups(other), [])


if __name__ == '__main__':
    unittest.main()
