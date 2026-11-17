# Daily Journal

Flask, SQLite, Jinja templates, and vanilla JavaScript. The main journal body uses
Quill 2.0.3; titles, moods, and optional Daily Reflection answers remain plain text.
Quill, Lucide icons and the Literata font are vendored under `static/vendor/`, so
the app works fully offline. All pages extend `templates/base.html`; shared
pieces (mood picker, reflection fields, editor) live in `templates/_macros.html`.
Mood names, emoji and colors are defined once in `MOODS` in `views/shared.py`.

## Project layout

| File | What it does |
| --- | --- |
| `app.py` | Creates the app (`create_app()`), registers the blueprints, runs the schema check and the daily backup before requests |
| `config.py` | Reads settings from environment variables and `.env` |
| `db.py` | `get_db()`: one SQLite connection per request, closed automatically |
| `views/entries.py` | Home, Write, Edit, an entry, favorites, delete/restore, Trash |
| `views/history.py` | History calendar, search and filters |
| `views/insights.py` | Insights and the weekly review |
| `views/backup.py` | Backup & export and JSON restore |
| `database.py` | Creates and upgrades the schema |
| `backup.py`, `tags.py`, `memories.py`, `weekly.py`, `journal_insights.py`, `trash.py` | Logic and SQL; each function takes a connection |

Routes get their connection from `get_db()` and pass it to the helper modules,
which never open their own. The only exceptions are schema setup (`database.py`)
and the file copies made for backups and the `.db` download (`backup.py`).
Every query passes values as `?` parameters.

Endpoints are named after their blueprint, so templates link with
`url_for('entries.view_entry', ...)`, `url_for('history.history')`,
`url_for('insights.week')` and `url_for('backup.backup_page')`.

## Setup

Run these commands from this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m flask --app app init-db
```

`.env` holds your local settings and is ignored by git. Every variable is optional
for local use:

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_ENV` | `development` | `development` or `production` |
| `SECRET_KEY` | a fixed development key | Signs the session cookie ("Entry saved." messages, and logins later) |
| `DATABASE` | `journal.db` | Path to the SQLite file. The older `JOURNAL_DATABASE` name still works |

Variables set in your shell take priority over `.env`. In production
(`APP_ENV=production`) the app refuses to start unless `SECRET_KEY` is set to a
real secret. Generate one with:

```sh
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```

The `init-db` command adds missing columns without replacing existing rows. The same
upgrade also runs before each request, so routes never use an outdated schema.
Importing the app does not upgrade the database.

## Running the app

Stop any running development server first, then:

```sh
.venv/bin/python app.py
```

The server runs at http://127.0.0.1:5001, with Flask's debugger on in development
and off in production. `.venv/bin/python -m flask --app app run --port 5001` works
too. Flask's built-in server is for local use only. To deploy, set `APP_ENV=production`
and `SECRET_KEY`, then serve `app:app` with a WSGI server such as gunicorn.

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

## Running the tests

```sh
.venv/bin/python -m unittest discover -s tests -v
for test in tests/*.js; do node "$test"; done
node --check static/journal-editor.js static/write-extras.js
```

Python tests create temporary databases and verify the real database checksum is
unchanged. JavaScript tests use DOM/editor doubles for integration logic; real
browser toolbar and visual checks are covered by the manual steps above.
