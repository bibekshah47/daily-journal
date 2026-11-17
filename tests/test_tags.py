"""Tags: rules, saving, History filtering and backups (temporary databases only)."""
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

from database import connect, initialize_database
from tags import (all_tags_with_counts, get_tags_for_entries, normalize_tag,
                  parse_tags, set_entry_tags)
import backup


class TagRuleTests(unittest.TestCase):
    def test_normalize(self):
        cases = {
            '  Work  ': 'work',
            '#Deep Work': 'deep-work',
            'deep   work': 'deep-work',
            '##hash': 'hash',
            'a+b/c!?': 'abc',
            'snake_case-ok': 'snake_case-ok',
            'Café 2026': 'café-2026',
            '#': '',
            '   ': '',
            '<script>': 'script',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_tag(raw), expected)

    def test_max_length_is_30(self):
        self.assertEqual(normalize_tag('x' * 45), 'x' * 30)

    def test_parse_drops_empties_and_duplicates(self):
        self.assertEqual(parse_tags('Work, , #work, WORK,  family ,,'), ['work', 'family'])
        self.assertEqual(parse_tags(''), [])
        self.assertEqual(parse_tags(None), [])

    def test_at_most_five_tags(self):
        self.assertEqual(parse_tags('a, b, a, c, d, e, f, g'), ['a', 'b', 'c', 'd', 'e'])


class TagAppTests(unittest.TestCase):
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
        folder = Path(self.tmp.name) / name.replace('.db', '')
        folder.mkdir()
        path = str(folder / name)
        initialize_database(path)
        return path

    def use_database(self, path):
        self.app.config.update(TESTING=True, DATABASE=path)
        self.client = self.app.test_client()

    def create(self, journal='Plain body', **fields):
        response = self.client.post('/write', data={'journal': journal, **fields})
        self.assertEqual(response.status_code, 302)
        return int(response.location.split('/entry/')[1].split('?')[0])

    def tags_of(self, entry_id, path=None):
        db = connect(path or self.path)
        try:
            return get_tags_for_entries(db, [entry_id])[entry_id]
        finally:
            db.close()

    def tag_names(self, path=None):
        db = connect(path or self.path)
        try:
            return sorted(row[0] for row in db.execute('SELECT name FROM tags'))
        finally:
            db.close()

    def history_ids(self, **params):
        page = self.client.get('/history', query_string=params)
        self.assertEqual(page.status_code, 200)
        return page, {int(n) for n in re.findall(r'id="entry-(\d+)"', page.text)}

    def restore(self, payload):
        data = json.dumps(payload).encode()
        return self.client.post('/backup/restore',
                                data={'backup_file': (io.BytesIO(data), 'backup.json')},
                                content_type='multipart/form-data', follow_redirects=True)

    # ---------- schema ----------

    def test_upgrade_keeps_existing_entries_and_enables_foreign_keys(self):
        legacy = str(Path(self.tmp.name) / 'legacy.db')
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE entries (id INTEGER PRIMARY KEY, content TEXT NOT NULL, '
                       'created_at TEXT NOT NULL, mood TEXT)')
            db.execute("INSERT INTO entries VALUES (4, 'Old words', '2026-11-01T09:00:00', 'Sad')")
        db.close()
        initialize_database(legacy)
        initialize_database(legacy)
        db = connect(legacy)
        try:
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT content FROM entries WHERE id = 4').fetchone()[0],
                             'Old words')
            set_entry_tags(db, 4, ['legacy'])
            db.commit()
            self.assertEqual(all_tags_with_counts(db), [{'name': 'legacy', 'count': 1}])
        finally:
            db.close()

    # ---------- write and edit ----------

    def test_write_saves_normalized_tags(self):
        entry_id = self.create(tags='Work, #Deep Work, work, , a, b, c, d')
        self.assertEqual(self.tags_of(entry_id), ['work', 'deep-work', 'a', 'b', 'c'])

    def test_write_page_has_tag_field_and_suggestions(self):
        self.create(tags='reading')
        page = self.client.get('/write').text
        self.assertIn('<input type="hidden" id="tags" name="tags" value="">', page)
        self.assertIn('placeholder="Add a tag"', page)
        self.assertIn('Press Enter to add. Up to 5.', page)
        self.assertIn('<option value="reading">', page)
        # The field sits below the mood picker.
        self.assertLess(page.index('class="mood-picker"'), page.index('class="tag-field"'))

    def test_failed_write_keeps_submitted_tags(self):
        response = self.client.post('/write', data={'journal': '  ', 'tags': 'Keep Me'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('name="tags" value="keep-me"', response.text)

    def test_edit_changes_tags(self):
        entry_id = self.create(tags='work, family')
        page = self.client.get(f'/edit/{entry_id}').text
        self.assertIn('name="tags" value="work, family"', page)

        response = self.client.post(f'/edit/{entry_id}', data={'journal': 'Changed', 'tags': 'family, travel'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.tags_of(entry_id), ['family', 'travel'])
        self.assertEqual(self.tag_names(), ['family', 'travel'])   # "work" is unused now

        self.client.post(f'/edit/{entry_id}', data={'journal': 'Changed', 'tags': ''})
        self.assertEqual(self.tags_of(entry_id), [])
        self.assertEqual(self.tag_names(), [])

    def test_failed_edit_keeps_tags_unchanged_in_database(self):
        entry_id = self.create(tags='work')
        response = self.client.post(f'/edit/{entry_id}', data={'journal': '', 'tags': 'other'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('name="tags" value="other"', response.text)
        self.assertEqual(self.tags_of(entry_id), ['work'])

    # ---------- delete ----------

    def test_delete_removes_links_and_unused_tags(self):
        first = self.create(tags='shared, only-first')
        second = self.create(tags='shared')
        self.client.post(f'/delete/{first}')
        # In Trash the entry keeps its tags, so Restore brings them back.
        self.assertEqual(self.tags_of(first), ['shared', 'only-first'])
        self.client.post(f'/trash/{first}/purge')
        db = connect(self.path)
        try:
            links = db.execute('SELECT COUNT(*) FROM entry_tags WHERE entry_id = ?', (first,)).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(links, 0)
        self.assertEqual(self.tag_names(), ['shared'])
        self.assertEqual(self.tags_of(second), ['shared'])

    # ---------- showing tags ----------

    def test_entry_home_and_history_show_tag_pills(self):
        entry_id = self.create(tags='work, deep-work')
        entry = self.client.get(f'/entry/{entry_id}').text
        self.assertIn('class="tag-pill" href="/history?tag=work"', entry)
        self.assertIn('class="tag-pill" href="/history?tag=deep-work"', entry)

        home = self.client.get('/').text
        self.assertIn('<span class="tag-pill"><span aria-hidden="true">#</span>work</span>', home)
        self.assertNotIn('href="/history?tag=work"', home)   # no links nested inside the card link

        history = self.client.get('/history').text
        self.assertIn('class="tag-pill" href="/history?tag=work"', history)

    # ---------- History filter ----------

    def test_history_tag_filter_combines_with_search_and_favorites(self):
        work_a = self.create('Quarterly planning', tags='work')
        work_b = self.create('Lunch with the team', tags='work, food')
        food = self.create('Planning dinner', tags='food')
        self.client.post(f'/favorite/{work_b}')

        _, ids = self.history_ids(tag='work')
        self.assertEqual(ids, {work_a, work_b})
        _, ids = self.history_ids(tag='#Work')          # normalized like a saved tag
        self.assertEqual(ids, {work_a, work_b})
        _, ids = self.history_ids(tag='work', search='planning')
        self.assertEqual(ids, {work_a})
        _, ids = self.history_ids(tag='work', favorites='1')
        self.assertEqual(ids, {work_b})
        _, ids = self.history_ids(tag='nothing-here')
        self.assertEqual(ids, set())
        _, ids = self.history_ids(search='planning')
        self.assertEqual(ids, {work_a, food})

    def test_search_matches_tag_names(self):
        tagged = self.create('Nothing about it here', tags='gardening')
        self.create('Unrelated')
        _, ids = self.history_ids(search='garden')
        self.assertEqual(ids, {tagged})
        _, ids = self.history_ids(search='#gardening')
        self.assertEqual(ids, {tagged})

    def test_history_keeps_tag_in_links_and_forms(self):
        entry_id = self.create(tags='work')
        page, _ = self.history_ids(tag='work', month='2026-11', search='body')
        text = page.text.replace('&amp;', '&')
        self.assertIn('aria-label="Remove tag filter #work"', text)
        self.assertIn('<input type="hidden" name="tag" value="work">', text)    # search + favorite forms
        self.assertEqual(text.count('<input type="hidden" name="tag" value="work">'), 2)
        self.assertIn('month=2026-10&tag=work', text)     # previous month arrow
        self.assertIn('month=2026-12&tag=work', text)     # next month arrow
        self.assertRegex(text, r'href="/history\?[^"]*favorites=1[^"]*tag=work"')   # Favorites
        self.assertRegex(text, r'href="/history\?search=body&month=2026-11&tag=work"')   # All
        self.assertRegex(text, r'filter_date=2026-11-01[^"]*tag=work"')   # calendar days
        # Removing the tag filter keeps the search.
        self.assertIn('href="/history?search=body&month=2026-11"', text)

        response = self.client.post(f'/favorite/{entry_id}', data={'tag': 'work', 'month': '2026-11'})
        self.assertIn('tag=work', response.location)

    # ---------- backup ----------

    def test_json_and_markdown_exports_include_tags(self):
        self.create('Tagged entry', tags='work, family')
        self.create('No tags here')
        payload = self.client.get('/backup/export.json').get_json()
        self.assertEqual(payload['version'], 2)
        by_content = {entry['content']: entry for entry in payload['entries']}
        self.assertEqual(by_content['Tagged entry']['tags'], ['work', 'family'])
        self.assertEqual(by_content['No tags here']['tags'], [])

        text = self.client.get('/backup/export.md').get_data(as_text=True)
        self.assertIn('Tags: work, family', text)
        self.assertEqual(text.count('Tags:'), 1)

    def test_restoring_version_2_restores_tags(self):
        self.create('Tagged entry', tags='work, family')
        self.create('No tags here')
        exported = self.client.get('/backup/export.json').get_json()

        empty = self.new_database('empty.db')
        self.use_database(empty)
        page = self.restore(exported)
        self.assertIn('Restored 2 entries.', page.text)
        db = connect(empty)
        try:
            ids = {content: entry_id for entry_id, content in db.execute('SELECT id, content FROM entries')}
        finally:
            db.close()
        self.assertEqual(self.tags_of(ids['Tagged entry'], empty), ['work', 'family'])
        self.assertEqual(self.tags_of(ids['No tags here'], empty), [])

    def test_restore_applies_tag_rules_and_leaves_existing_entries_alone(self):
        existing = self.create('Already here', tags='mine')
        db = connect(self.path)
        try:
            created_at = db.execute('SELECT created_at FROM entries WHERE id = ?',
                                    (existing,)).fetchone()[0]
        finally:
            db.close()
        payload = {'app': 'my-journal', 'version': 2, 'entries': [
            {'content': 'Already here', 'created_at': created_at, 'tags': ['theirs']},
            {'content': 'New one', 'created_at': '2026-11-10T10:00:00',
             'tags': ['#Big Day', 'big-day', '', 'a', 'b', 'c', 'd', 'e']},
        ]}
        page = self.restore(payload)
        self.assertIn('Restored 1 entry. Skipped 1 that was already here.', page.text)
        self.assertEqual(self.tags_of(existing), ['mine'])
        db = connect(self.path)
        try:
            new_id = db.execute("SELECT id FROM entries WHERE content = 'New one'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(self.tags_of(new_id), ['big-day', 'a', 'b', 'c', 'd'])
        self.assertNotIn('theirs', self.tag_names())

    def test_version_1_files_still_restore(self):
        payload = {'app': 'my-journal', 'version': 1, 'entries': [
            # A stray "tags" key in a version 1 file is ignored.
            {'content': 'From the old format', 'created_at': '2026-10-01T08:00:00',
             'title': 'Old', 'tags': ['ignored']},
        ]}
        page = self.restore(payload)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Restored 1 entry.', page.text)
        self.assertEqual(self.tag_names(), [])
        self.assertEqual(len(backup.parse_backup(json.dumps(payload).encode())), 1)

    def test_invalid_tags_reject_the_whole_file(self):
        for tags in ['work', [1, 2], {'a': 1}]:
            with self.subTest(tags=tags):
                payload = {'app': 'my-journal', 'version': 2, 'entries': [
                    {'content': 'x', 'created_at': '2026-11-10T10:00:00', 'tags': tags}]}
                response = self.restore(payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn('invalid list of tags', response.text)


if __name__ == '__main__':
    unittest.main()
