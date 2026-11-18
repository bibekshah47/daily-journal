"""The database layer: placeholders, rows, migrations, and copying SQLite to PostgreSQL.

Runs on SQLite by default; with TEST_DB=postgres the same tests run on
PostgreSQL, plus the copy tests, which need both databases.
"""
import importlib
import os
import tempfile
import unittest

from accounts import create_user
from database import IntegrityError, connect, initialize_database, is_postgres, to_postgres_sql
import migrations
from tags import get_tags_for_entries, set_entry_tags
from tests.helpers import (POSTGRES, SECURITY_OFF, TEST_PASSWORD, add_user, empty_test_database,
                           log_in_as, new_test_database, postgres_only, skip_daily_maintenance,
                           sqlite_only)


class PlaceholderTests(unittest.TestCase):
    def test_question_marks_become_percent_s(self):
        self.assertEqual(to_postgres_sql('SELECT * FROM t WHERE a = ? AND b IN (?, ?)'),
                         'SELECT * FROM t WHERE a = %s AND b IN (%s, %s)')

    def test_question_marks_inside_quotes_are_left_alone(self):
        self.assertEqual(to_postgres_sql("SELECT 'why?', ? FROM t WHERE note = 'it''s ok?' AND x = ?"),
                         "SELECT 'why?', %s FROM t WHERE note = 'it''s ok?' AND x = %s")
        self.assertEqual(to_postgres_sql('SELECT "odd?column" FROM t WHERE a = ?'),
                         'SELECT "odd?column" FROM t WHERE a = %s')

    def test_comments_are_left_alone(self):
        self.assertEqual(to_postgres_sql('SELECT ? -- really?\nFROM t WHERE a = ?'),
                         'SELECT %s -- really?\nFROM t WHERE a = %s')

    def test_percent_signs_are_escaped_everywhere(self):
        self.assertEqual(to_postgres_sql("SELECT * FROM t WHERE a LIKE '%x%' AND b = ? AND c % 2 = 0"),
                         "SELECT * FROM t WHERE a LIKE '%%x%%' AND b = %s AND c %% 2 = 0")

    def test_backend_is_chosen_by_the_target(self):
        for target in ('postgres://u@h/db', 'postgresql://u@h/db?sslmode=require'):
            self.assertTrue(is_postgres(target))
        for target in ('journal.db', '/data/journal.db', '', None):
            self.assertFalse(is_postgres(target))


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.path = new_test_database(self)
        self.db = connect(self.path)
        self.addCleanup(self.db.close)

    def test_rows_work_by_index_by_name_and_as_tuples(self):
        row = self.db.fetchone("SELECT 1 AS one, 'two' AS two, ? AS three", ('3',))
        self.assertEqual((row[0], row['two'], row[-1]), (1, 'two', '3'))
        self.assertEqual(row, (1, 'two', '3'))
        self.assertEqual(dict(row), {'one': 1, 'two': 'two', 'three': '3'})
        one, two, three = row
        self.assertEqual(two, 'two')

    def test_insert_returns_the_new_id(self):
        first = self.db.insert("INSERT INTO users (email, password_hash, created_at) VALUES (?, 'h', ?)",
                               ('a@example.com', '2026-01-01'))
        second = self.db.insert("INSERT INTO users (email, password_hash, created_at) VALUES (?, 'h', ?)",
                                ('b@example.com', '2026-01-01'))
        self.assertEqual(second, first + 1)
        self.assertEqual(self.db.fetchone('SELECT email FROM users WHERE id = ?', (second,))[0],
                         'b@example.com')

    def test_duplicate_is_an_integrity_error_and_the_transaction_recovers(self):
        create_user(self.db, 'a@example.com', TEST_PASSWORD)
        with self.assertRaises(IntegrityError):
            self.db.insert("INSERT INTO users (email, password_hash, created_at) VALUES (?, 'h', '2026')",
                           ('a@example.com',))
        self.db.rollback()
        self.assertEqual(self.db.fetchone('SELECT COUNT(*) FROM users')[0], 0)

    def test_with_block_commits_or_rolls_back_and_stays_open(self):
        with self.db:
            self.db.execute("INSERT INTO users (email, password_hash, created_at) VALUES ('a@x.com', 'h', '1')")
        with self.assertRaises(ZeroDivisionError):
            with self.db:
                self.db.execute("INSERT INTO users (email, password_hash, created_at) VALUES ('b@x.com', 'h', '1')")
                1 / 0
        self.assertEqual([row[0] for row in self.db.fetchall('SELECT email FROM users')], ['a@x.com'])

    def test_tags_keep_the_order_they_were_added(self):
        user_id = add_user(self.path)
        entry_id = self.db.insert("INSERT INTO entries (content, created_at, user_id) VALUES ('x', '2026', ?)",
                                  (user_id,))
        set_entry_tags(self.db, entry_id, ['zebra', 'apple', 'mango'], user_id=user_id)
        self.assertEqual(get_tags_for_entries(self.db, [entry_id], user_id=user_id)[entry_id],
                         ['zebra', 'apple', 'mango'])


class MigrationTests(unittest.TestCase):
    def tables(self, connection):
        if connection.is_postgres:
            sql = ("SELECT table_name FROM information_schema.tables "
                   "WHERE table_schema = current_schema()")
        else:
            sql = "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        return {row[0] for row in connection.fetchall(sql)}

    def test_migrations_run_once_and_are_idempotent(self):
        target = empty_test_database(self)
        self.assertEqual(initialize_database(target), [1, 2])
        self.assertEqual(initialize_database(target), [])
        self.assertEqual(initialize_database(target), [])
        db = connect(target)
        try:
            self.assertEqual(db.fetchall('SELECT version FROM schema_migrations ORDER BY version'),
                             [(1,), (2,)])
            self.assertLessEqual({'users', 'entries', 'tags', 'entry_tags', 'live_entries',
                                  'login_throttle', 'schema_migrations'}, self.tables(db))
            self.assertEqual(migrations.LATEST_VERSION, 2)
        finally:
            db.close()

    def test_new_ids_are_never_reused(self):
        target = new_test_database(self)
        db = connect(target)
        try:
            first = create_user(db, 'a@example.com', TEST_PASSWORD)
            db.execute('DELETE FROM users WHERE id = ?', (first,))
            second = create_user(db, 'b@example.com', TEST_PASSWORD)
            db.commit()
            self.assertGreater(second, first)
        finally:
            db.close()

    def test_deleting_a_user_cascades(self):
        target = new_test_database(self)
        user_id = add_user(target)
        db = connect(target)
        try:
            entry_id = db.insert("INSERT INTO entries (content, created_at, user_id) VALUES ('x', '2026', ?)",
                                 (user_id,))
            set_entry_tags(db, entry_id, ['work'], user_id=user_id)
            db.execute('DELETE FROM users WHERE id = ?', (user_id,))
            db.commit()
            for table in ('entries', 'tags', 'entry_tags'):
                self.assertEqual(db.fetchone(f'SELECT COUNT(*) FROM {table}')[0], 0, table)
        finally:
            db.close()

    @sqlite_only
    def test_existing_sqlite_file_is_marked_version_1_without_changes(self):
        """A journal file made before migrations (by the old per-request
        upgrade) is recorded as version 1; only migration 2 runs."""
        path = empty_test_database(self)
        old = connect(path)
        migrations._upgrade_pre_migration_sqlite(old)   # what the app used to do
        old.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (4, 'a@x.com', 'h', '1')")
        old.execute("INSERT INTO entries (id, content, created_at, user_id) VALUES (9, 'Kept', '2026', 4)")
        old.execute("INSERT INTO tags (id, user_id, name) VALUES (3, 4, 'b')")
        old.execute("INSERT INTO tags (id, user_id, name) VALUES (2, 4, 'a')")
        old.execute('INSERT INTO entry_tags (entry_id, tag_id) VALUES (9, 3)')
        old.execute('INSERT INTO entry_tags (entry_id, tag_id) VALUES (9, 2)')
        old.commit()
        schema_before = old.fetchall("SELECT type, name, sql FROM sqlite_master "
                                     "WHERE name NOT LIKE 'sqlite_%' ORDER BY name")
        old.close()

        self.assertEqual(initialize_database(path), [2])
        db = connect(path)
        try:
            self.assertEqual(db.fetchall('SELECT version FROM schema_migrations ORDER BY version'),
                             [(1,), (2,)])
            self.assertEqual(db.fetchall('SELECT id, content, user_id FROM entries'), [(9, 'Kept', 4)])
            # Nothing but migration 2's column changed.
            schema_after = db.fetchall("SELECT type, name, sql FROM sqlite_master "
                                       "WHERE name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' "
                                       "ORDER BY name")
            changed = [after for before, after in zip(schema_before, schema_after) if before != after]
            self.assertEqual([row[1] for row in changed], ['entry_tags'])
            self.assertEqual(get_tags_for_entries(db, [9], user_id=4), {9: ['b', 'a']})
        finally:
            db.close()

    @sqlite_only
    def test_migration_1_matches_the_old_upgraded_schema(self):
        fresh, upgraded = empty_test_database(self, 'fresh.db'), empty_test_database(self, 'old.db')
        initialize_database(fresh)
        old = connect(upgraded)
        migrations._upgrade_pre_migration_sqlite(old)
        old.close()
        initialize_database(upgraded)
        a, b = connect(fresh), connect(upgraded)
        try:
            for table in ('users', 'entries', 'tags', 'entry_tags', 'login_throttle'):
                columns = lambda db: [tuple(row)[1:] for row in db.fetchall(f'PRAGMA table_info({table})')]
                self.assertEqual(columns(a), columns(b), table)
        finally:
            a.close()
            b.close()


class PortableQueryTests(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module('app')
        self.path = new_test_database(self)
        self.app = module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': self.path})
        skip_daily_maintenance(module, self.path)
        self.user_id = add_user(self.path)
        self.client = self.app.test_client()
        log_in_as(self.client, self.user_id)

    def test_search_ignores_case_on_every_database(self):
        self.client.post('/write', data={'title': 'Morning Walk', 'journal': 'Saw a HERON',
                                         'tags': 'Outdoors'})
        for query in ('morning', 'MORNING', 'heron', 'outdoors', '#OUTDOORS'):
            with self.subTest(query=query):
                self.assertIn('Morning Walk', self.client.get('/history', query_string={'search': query}).text)
        self.assertNotIn('Morning Walk', self.client.get('/history?search=evening').text)

    def test_backup_page_mentions_file_backups_only_with_sqlite(self):
        page = self.client.get('/backup').text
        self.assertEqual('Last automatic backup' in page, not POSTGRES)
        self.assertEqual('stat-band-3' in page, POSTGRES)


@postgres_only
class CopySqliteToPostgresTests(unittest.TestCase):
    def make_sqlite_journal(self):
        """A SQLite journal file with two users, entries, tags, and a gap in the ids."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, 'journal.db')
        migrations.migrate(path)
        db = connect(path)
        ann = create_user(db, 'ann@example.com', TEST_PASSWORD, 'Ann')
        gone = create_user(db, 'gone@example.com', TEST_PASSWORD)
        bob = create_user(db, 'bob@example.com', TEST_PASSWORD)
        db.execute('DELETE FROM users WHERE id = ?', (gone,))
        for user_id, count in ((ann, 3), (bob, 1)):
            for number in range(count):
                entry_id = db.insert("INSERT INTO entries (content, created_at, user_id, is_favorite) "
                                     "VALUES (?, ?, ?, ?)",
                                     (f'Entry {number}', f'2026-0{number + 1}-01T09:00:00', user_id,
                                      number % 2))
                set_entry_tags(db, entry_id, ['work', f'tag-{number}'], user_id=user_id)
        db.commit()
        db.close()
        return path, ann, bob

    def setUp(self):
        self.module = importlib.import_module('app')
        self.target = new_test_database(self)
        self.app = self.module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': self.target})

    def copy(self, sqlite_path):
        return self.app.test_cli_runner().invoke(
            args=['copy-sqlite-to-postgres', '--sqlite', sqlite_path])

    def test_copies_everything_keeping_ids_and_resets_sequences(self):
        source, ann, bob = self.make_sqlite_journal()
        result = self.copy(source)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Copied 2 users, 4 entries, 5 tags, 8 entry_tags.', result.output)

        src, dst = connect(source), connect(self.target)
        try:
            for table, order in (('users', 'id'), ('entries', 'id'), ('tags', 'id'),
                                 ('entry_tags', 'entry_id, tag_id')):
                self.assertEqual([tuple(row) for row in src.fetchall(f'SELECT * FROM {table} ORDER BY {order}')],
                                 [tuple(row) for row in dst.fetchall(f'SELECT * FROM {table} ORDER BY {order}')],
                                 table)
            # New rows continue after the highest copied id.
            new_user = create_user(dst, 'new@example.com', TEST_PASSWORD)
            self.assertEqual(new_user, bob + 1)
            new_entry = dst.insert("INSERT INTO entries (content, created_at, user_id) VALUES ('n', '2026', ?)",
                                   (ann,))
            self.assertEqual(new_entry, src.fetchone('SELECT MAX(id) FROM entries')[0] + 1)
            dst.commit()
        finally:
            src.close()
            dst.close()

        # The copied account works: log in and see the entries.
        client = self.app.test_client()
        self.module._daily_backups_checked.add(self.module.maintenance_key(self.target))
        response = client.post('/login', data={'email': 'ann@example.com', 'password': TEST_PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertIn('Entry 2', client.get('/history').text)

    def test_refuses_a_target_that_already_has_users(self):
        source, _, _ = self.make_sqlite_journal()
        add_user(self.target, 'existing@example.com')
        result = self.copy(source)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('already has users or entries', result.output)
        db = connect(self.target)
        try:
            self.assertEqual(db.fetchone('SELECT COUNT(*) FROM users')[0], 1)
            self.assertEqual(db.fetchone('SELECT COUNT(*) FROM entries')[0], 0)
        finally:
            db.close()


class CopyCommandOnSqliteTests(unittest.TestCase):
    @sqlite_only
    def test_refuses_when_the_configured_database_is_not_postgres(self):
        module = importlib.import_module('app')
        target = new_test_database(self)
        app = module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': target})
        result = app.test_cli_runner().invoke(args=['copy-sqlite-to-postgres', '--sqlite', target])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("isn't PostgreSQL", result.output)

    def test_init_db_reports_what_it_did(self):
        module = importlib.import_module('app')
        target = new_test_database(self)
        app = module.create_app({'TESTING': True, **SECURITY_OFF, 'DATABASE': target})
        result = app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('database is up to date', result.output)


if __name__ == '__main__':
    unittest.main()
