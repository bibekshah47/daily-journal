# Daily Journal

Flask, SQLite, Jinja templates, and vanilla JavaScript. The main journal body uses
Quill 2.0.3; titles, moods, and optional Daily Reflection answers remain plain text.
Quill, Lucide icons and the Literata font are vendored under `static/vendor/`, so
the app works fully offline. All pages extend `templates/base.html`; shared
pieces (mood picker, reflection fields, editor) live in `templates/_macros.html`.
Mood names, emoji and colors are defined once in `MOODS` in `app.py`.

Set `SECRET_KEY` in your environment to keep confirmation messages
("Entry saved.") working across server restarts.

## Setup or upgrade

Stop any running development server, then run these commands from this directory:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m flask --app app init-db
.venv/bin/python app.py
```

The explicit `init-db` command adds missing columns without replacing existing rows.
The same migration also runs before each request, so routes never use an outdated
schema. Application imports alone do not migrate the database. The default
SQLite file is `journal.db`; set `JOURNAL_DATABASE` to use another file. The server
runs at http://127.0.0.1:5001.

`content` stays plain text for previews and search. `content_html` holds sanitized
formatting and is NULL on legacy entries. Legacy content is never interpreted as
HTML. Sanitization runs on submission and when rendering formatted content.

## Backup & export

The **Backup & export** link at the bottom of the sidebar opens `/backup`, which
offers three downloads: a readable Markdown file, a JSON file with every column of
every entry, and an exact copy of the SQLite database. A JSON export can be
restored on the same page. Restoring only adds entries that aren't already present
(same `created_at` and `content`); it never overwrites or deletes anything.

On the first request each day the app also copies the database to
`backups/journal-YYYY-MM-DD.db`, next to the database file, and keeps the newest 14.
A failed backup is logged and never stops a page from loading. The `backups/`
folder is ignored by git.

## Manual checks

1. Write an entry with bold, italic, underline, headings, both list types, a quote,
   and a link. Save, read, edit, and save again; confirm formatting survives.
2. Open an older plain-text entry. Check line breaks and literal angle brackets,
   then edit and save it.
3. Write a formatted draft and reflection answers. Wait a second, refresh, and
   confirm both text and formatting return. Try Discard Draft and then save a
   fresh entry; reopening Write Journal should not restore the saved draft.
4. Try saving an empty editor. Paste formatted text and check its appearance.
5. Check search and previews show readable text, and verify calendar filters,
   favorites, dashboard statistics, and reflection editing.
6. Check a narrow window and keyboard navigation. Blocking the Quill script should
   leave a usable plain-text textarea; saving through that fallback uses plain text.

Autosave applies to the new-entry page, as before. The edit page does not autosave.
Drafts remain local to the browser, and old plain-text drafts still restore.

## Automated checks

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/test_editor.js
node --check static/journal-editor.js
```

Python tests create temporary databases and verify the real database checksum is
unchanged. JavaScript tests use DOM/editor doubles for integration logic; real
browser toolbar and visual checks are covered by the manual steps above.
