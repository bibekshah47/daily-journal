"""Trash with undo, using temporary databases only."""
from datetime import date, datetime, timedelta
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
from unittest import mock

import backup
from database import connect, initialize_database
from journal_insights import get_journal_stats
from memories import get_memories, subtract_months
from tags import all_tags_with_counts, set_entry_tags
from trash import cleanup_trash
from tests.helpers import SECURITY_OFF, new_test_database, signed_in_client, skip_daily_maintenance, sqlite_only


class TrashTests(unittest.TestCase):
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
        self.path = self.new_database('journal.db')
        self.use_database(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    # ---------- helpers ----------

    def new_database(self, name):
        return new_test_database(self, name)

    def use_database(self, path):
        self.app.config.update(TESTING=True, **SECURITY_OFF, DATABASE=path)
        self.client, self.user_id = signed_in_client(self.app, path)
        # Skip the once-a-day backup/cleanup hook unless a test asks for it.
        skip_daily_maintenance(self.module, path, date.today())

    def add(self, content, created_at, title=None, mood=None, tags=None, deleted_at=None):
        """Insert directly, so tests control dates."""
        db = connect(self.path)
        try:
            entry_id = db.insert(
                'INSERT INTO entries (title, content, mood, created_at, deleted_at, user_id) '
                'VALUES (?, ?, ?, ?, ?, ?)', (title, content, mood, created_at, deleted_at, self.user_id))
            if tags:
                set_entry_tags(db, entry_id, tags, user_id=self.user_id)
            db.commit()
            return entry_id
        finally:
            db.close()

    def query(self, sql, *params):
        db = connect(self.path)
        try:
            return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def deleted_at(self, entry_id):
        rows = self.query('SELECT deleted_at FROM entries WHERE id = ?', entry_id)
        return rows[0][0] if rows else 'erased'

    def history_ids(self, **params):
        text = self.client.get('/history', query_string=params).text
        return {int(n) for n in re.findall(r'id="entry-(\d+)"', text)}

    def trashed_entry(self):
        """A live entry and a trashed one, written today and a year ago."""
        today = date.today()
        year_ago = subtract_months(today, 12)
        self.keep = self.add('Keep this walk', f'{today.isoformat()}T08:00:00', 'Keeper',
                             'Happy', ['walks'])
        self.gone = self.add('Secret plan', f'{year_ago.isoformat()}T09:00:00', 'Hidden one',
                             'Stressed', ['secret', 'walks'])
        self.client.post(f'/favorite/{self.gone}')
        return self.gone

    # ---------- delete and undo ----------

    def test_delete_moves_to_trash_with_an_undo_toast(self):
        entry_id = self.add('Words', '2026-11-01T09:00:00', 'Soon gone')
        response = self.client.post(f'/delete/{entry_id}')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/history'))
        self.assertIsNotNone(datetime.fromisoformat(self.deleted_at(entry_id)))

        page = self.client.get('/history').text
        self.assertIn('Entry moved to Trash.', page)
        self.assertIn('<div class="toast-region" role="status" aria-live="polite">', page)
        self.assertIn('class="toast toast-has-action"', page)
        self.assertIn(f'action="/restore/{entry_id}" method="POST"', page)
        self.assertIn('<input type="hidden" name="next" value="/history">', page)
        self.assertIn('<button type="submit" class="toast-button">Undo</button>', page)
        self.assertIn('hasAction ? 8000 : 3500', page)

        # Undo restores it and goes back to the page that showed the toast.
        response = self.client.post(f'/restore/{entry_id}', data={'next': '/history'})
        self.assertEqual(response.location, '/history')
        self.assertIsNone(self.deleted_at(entry_id))
        page = self.client.get('/history').text
        self.assertIn('Entry restored.', page)
        self.assertNotIn('class="toast toast-has-action"', page)
        self.assertIn(f'id="entry-{entry_id}"', page)

    def test_plain_toasts_have_no_action(self):
        response = self.client.post('/write', data={'journal': 'Hello'}, follow_redirects=True)
        self.assertIn('Entry saved.', response.text)
        self.assertNotIn('class="toast toast-has-action"', response.text)
        self.assertNotIn('class="toast-button"', response.text)

    def test_delete_dialog_mentions_trash(self):
        entry_id = self.add('Words', '2026-11-01T09:00:00')
        page = self.client.get(f'/entry/{entry_id}').text
        self.assertIn('It moves to Trash. You can restore it for 30 days.', page)

    def test_restore_rejects_unsafe_next(self):
        entry_id = self.add('Words', '2026-11-01T09:00:00', deleted_at='2026-11-02T09:00:00')
        response = self.client.post(f'/restore/{entry_id}', data={'next': '//evil.example'})
        self.assertEqual(response.location, f'/entry/{entry_id}')

    # ---------- hidden everywhere ----------

    def test_trashed_entry_is_hidden_everywhere_then_restored(self):
        gone = self.trashed_entry()
        year_ago = subtract_months(date.today(), 12)
        self.client.post(f'/delete/{gone}')

        def visible():
            home = self.client.get('/').text
            history = self.client.get('/history', query_string={'month': year_ago.strftime('%Y-%m')}).text
            day_link = re.search(r'<a href="[^"]*filter_date=%s[^"]*"\s+class="([^"]*)"' % year_ago.isoformat(),
                                 history)
            db = connect(self.path)
            try:
                stats = get_journal_stats(db, date.today(), user_id=self.user_id)
                memories = [m['entry']['id'] for m in get_memories(db, date.today(), user_id=self.user_id)]
                tag_names = [t['name'] for t in all_tags_with_counts(db, user_id=self.user_id)]
            finally:
                db.close()
            insights = self.client.get('/insights').text
            stressed = re.search(r'Stressed.*?mood-bar-count">(\d+)<', insights, re.S)
            md = self.client.get('/backup/export.md').get_data(as_text=True)
            payload = self.client.get('/backup/export.json').get_json()
            return {
                'home recent / on this day': 'Hidden one' in home,
                'history list': gone in self.history_ids(),
                'calendar dot': 'has-entry' in day_link.group(1),
                'search': gone in self.history_ids(search='Secret'),
                'favorites': gone in self.history_ids(favorites='1'),
                'tag filter': gone in self.history_ids(tag='secret'),
                'tag counts': 'secret' in tag_names,
                'tag suggestions': '<option value="secret">' in self.client.get('/write').text,
                'insights stats': stats['total_entries'] == 2,
                'mood breakdown': stressed.group(1) == '1',
                'on this day': gone in memories,
                'markdown export': 'Secret plan' in md,
                'json export': any(e['content'] == 'Secret plan' for e in payload['entries']),
                'backup page count': '<p class="stat-value">2</p>' in self.client.get('/backup').text,
            }

        hidden = visible()
        for place, shown in hidden.items():
            with self.subTest(place=place, state='in trash'):
                self.assertFalse(shown)
        # The live entry is unaffected.
        self.assertIn(self.keep, self.history_ids(tag='walks'))
        self.assertIn('Keeper', self.client.get('/').text)

        self.client.post(f'/restore/{gone}')
        for place, shown in visible().items():
            with self.subTest(place=place, state='restored'):
                self.assertTrue(shown)

    def test_entry_pager_skips_trashed_entries(self):
        first = self.add('One', '2026-11-01T09:00:00', 'First')
        middle = self.add('Two', '2026-11-02T09:00:00', 'Middle')
        last = self.add('Three', '2026-11-03T09:00:00', 'Last')
        self.client.post(f'/delete/{middle}')
        page = self.client.get(f'/entry/{first}').text
        self.assertIn(f'href="/entry/{last}" class="pager-link pager-newer"', page)
        self.assertNotIn(f'/entry/{middle}"', page)

    # ---------- entry and edit pages ----------

    def test_trashed_entry_page_shows_notice_and_edit_redirects(self):
        entry_id = self.add('Original', '2026-11-01T09:00:00', 'Trashed',
                            deleted_at='2026-11-10T12:00:00')
        page = self.client.get(f'/entry/{entry_id}').text
        self.assertIn('This entry is in Trash', page)
        self.assertIn('erased on December 10, 2026', page)
        self.assertIn(f'action="/restore/{entry_id}"', page)
        self.assertNotIn(f'href="/edit/{entry_id}"', page)    # no Edit / Delete tools
        self.assertNotIn('id="delete-dialog"', page)

        response = self.client.get(f'/edit/{entry_id}')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, f'/entry/{entry_id}')
        response = self.client.post(f'/edit/{entry_id}', data={'journal': 'Changed'})
        self.assertEqual(response.location, f'/entry/{entry_id}')
        self.assertEqual(self.query('SELECT content FROM entries WHERE id = ?', entry_id)[0][0], 'Original')

        # Live entries are unchanged.
        live = self.add('Live', '2026-11-01T10:00:00')
        self.assertNotIn('This entry is in Trash', self.client.get(f'/entry/{live}').text)
        self.assertEqual(self.client.get(f'/edit/{live}').status_code, 200)

    # ---------- Trash page ----------

    def test_trash_page_lists_newest_deleted_first(self):
        now = datetime.now()
        older = self.add('Old', '2026-01-01T09:00:00', 'Deleted long ago', 'Sad',
                         deleted_at=(now - timedelta(days=29)).isoformat())
        newer = self.add('New', '2026-02-01T09:00:00', None,
                         deleted_at=(now - timedelta(days=2)).isoformat())
        self.add('Live', '2026-03-01T09:00:00', 'Not in trash')
        page = self.client.get('/trash').text
        self.assertIn('aria-current="page"', page)   # History stays active in the nav
        self.assertLess(page.index(f'id="entry-{newer}"'), page.index(f'id="entry-{older}"'))
        self.assertNotIn('Not in trash', page)
        self.assertIn('erased in 28 days', page)
        self.assertIn('erased in 1 day', page)
        self.assertIn('February 1, 2026', page)        # untitled -> date
        self.assertIn('mood-badge mood-sad', page)
        self.assertIn(f'action="/restore/{older}"', page)
        self.assertIn(f'data-action="/trash/{older}/purge"', page)
        self.assertIn('2 entries will be erased for good.', page)

    def test_empty_trash_page_and_history_link(self):
        page = self.client.get('/trash').text
        self.assertIn('Trash is empty', page)
        self.assertIn('Deleted entries stay here for 30 days.', page)
        self.assertNotIn('id="empty-open"', page)
        self.assertNotIn('href="/trash"', self.client.get('/history').text)

        entry_id = self.add('Words', '2026-11-01T09:00:00')
        self.client.post(f'/delete/{entry_id}')
        self.assertIn('Trash (1)', self.client.get('/history').text)

    # ---------- erasing ----------

    def test_purge_erases_permanently_and_removes_unused_tags(self):
        gone = self.trashed_entry()
        response = self.client.post(f'/trash/{gone}/purge')
        self.assertEqual(response.location, '/trash')
        self.assertEqual(self.deleted_at(gone), None)   # still live: purge only touches Trash

        self.client.post(f'/delete/{gone}')
        response = self.client.post(f'/trash/{gone}/purge', follow_redirects=True)
        self.assertIn('Entry deleted forever.', response.text)
        self.assertEqual(self.deleted_at(gone), 'erased')
        self.assertEqual(self.query('SELECT COUNT(*) FROM entry_tags WHERE entry_id = ?', gone)[0][0], 0)
        tags = sorted(row[0] for row in self.query('SELECT name FROM tags'))
        self.assertEqual(tags, ['walks'])        # still used by the live entry
        self.assertEqual(self.client.get(f'/entry/{gone}').status_code, 404)

    def test_empty_trash_erases_only_trashed_entries(self):
        gone = self.trashed_entry()
        other = self.add('Other', '2026-11-01T09:00:00', tags=['other'])
        self.client.post(f'/delete/{gone}')
        self.client.post(f'/delete/{other}')
        response = self.client.post('/trash/empty', follow_redirects=True)
        self.assertIn('Trash emptied.', response.text)
        self.assertIn('Trash is empty', response.text)
        self.assertEqual([row[0] for row in self.query('SELECT id FROM entries')], [self.keep])
        self.assertEqual([row[0] for row in self.query('SELECT name FROM tags')], ['walks'])

    def test_cleanup_erases_only_entries_older_than_30_days(self):
        # now is UTC; deleted_at is the owner's wall-clock time, which can be
        # ahead of UTC, so one extra day passes before anything is erased.
        now = datetime(2026, 11, 16, 12, 0, 0)
        expired = self.add('Expired', '2026-09-01T09:00:00', tags=['old-only'],
                           deleted_at='2026-10-16T11:59:59')
        recent = self.add('Recent', '2026-09-02T09:00:00',
                          deleted_at='2026-10-16T12:00:01')
        # Deleted at 13:00 Hawaii time (UTC-10), i.e. 23:00 UTC: only 29.5 days
        # ago, though the wall-clock text reads as more than 30 days: kept.
        behind = self.add('Kept', '2026-09-03T09:00:00', deleted_at='2026-10-16T13:00:00')
        live = self.add('Live', '2026-09-01T10:00:00')
        db = connect(self.path)
        try:
            with db:
                self.assertEqual(cleanup_trash(db, now), 1)
        finally:
            db.close()
        self.assertEqual(self.deleted_at(expired), 'erased')
        self.assertEqual(self.deleted_at(recent), '2026-10-16T12:00:01')
        self.assertEqual(self.deleted_at(behind), '2026-10-16T13:00:00')
        self.assertIsNone(self.deleted_at(live))
        self.assertEqual(self.query('SELECT name FROM tags'), [])

    @sqlite_only
    def test_daily_hook_cleans_up_after_backup_and_never_breaks_pages(self):
        old = (datetime.now() - timedelta(days=31)).isoformat()
        expired = self.add('Expired', '2026-01-01T09:00:00', deleted_at=old)
        self.module._daily_backups_checked.clear()
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.deleted_at(expired), 'erased')
        # The backup taken first still has it.
        [copy] = backup.list_backups(self.path)
        db = sqlite3.connect(copy)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 1)
        finally:
            db.close()

        self.module._daily_backups_checked.clear()
        with mock.patch.object(self.module, 'run_trash_cleanup', side_effect=sqlite3.OperationalError('locked')), \
                self.assertLogs(self.app.logger, 'ERROR') as logs:
            self.assertEqual(self.client.get('/history').status_code, 200)
        self.assertIn('Automatic trash cleanup failed', logs.output[0])

    # ---------- JSON restore ----------

    def test_json_restore_does_not_duplicate_a_trashed_entry(self):
        entry_id = self.add('Kept in backup', '2026-11-01T09:00:00', 'Backed up')
        exported = self.client.get('/backup/export.json').data
        self.client.post(f'/delete/{entry_id}')
        page = self.client.post('/backup/restore',
                                data={'backup_file': (io.BytesIO(exported), 'backup.json')},
                                content_type='multipart/form-data', follow_redirects=True)
        self.assertIn('Nothing new to restore. That entry was already here.', page.text)
        self.assertEqual(self.query('SELECT COUNT(*) FROM entries')[0][0], 1)
        self.assertIsNotNone(self.deleted_at(entry_id))   # still in Trash, not resurrected
        self.assertNotIn('deleted_at', json.loads(exported)['entries'][0])


if __name__ == '__main__':
    unittest.main()
