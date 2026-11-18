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
| `views/auth.py` | Sign up, log in, log out, forgot/reset password, and the login check on every other page |
| `views/settings.py` | Settings: name, email, password, delete account |
| `accounts.py` | Email and password rules, password hashing, reset tokens, claiming old entries |
| `security.py` | CSRF, rate limits, login lockout, security headers |
| `mailer.py` | Sends email (printed to the terminal in development) |
| `database.py` | Creates and upgrades the schema |
| `backup.py`, `tags.py`, `memories.py`, `weekly.py`, `journal_insights.py`, `trash.py` | Logic and SQL; each function takes a connection and the user's id |

Routes get their connection from `get_db()` and pass it to the helper modules,
which never open their own. The only exceptions are schema setup (`database.py`)
and the file copies made for daily backups (`backup.py`).
Every query passes values as `?` parameters.

## Accounts and privacy

Everyone has their own journal. Every page except `/login` and `/signup` needs
a logged-in user; logged-out visitors go to `/login?next=...` and come back
afterwards (`next` must be a path on this site, so links can't send people to
another site). Passwords are hashed with werkzeug (`generate_password_hash`)
and never stored, logged or shown.

Every query on entries and tags filters by the logged-in user's id, and the
helper functions require a `user_id` argument, so a query without one fails
loudly instead of returning someone else's data. Opening, editing,
favoriting, deleting, restoring or erasing another user's entry returns 404,
exactly like a missing entry. Tags belong to one user, so two people can both
have a `work` tag. Exports and restores only ever touch your own entries.
Unsaved Write drafts live in the browser under a key that includes the user id.

"Keep me logged in" lasts 30 days. Cookies are `HttpOnly` and `SameSite=Lax`,
and `Secure` when `APP_ENV=production`, so production must be served over HTTPS.
A session cookie used from a different IP address or browser is dropped
(Flask-Login "strong" protection), so moving networks can mean logging in again.

### Settings, sessions and password reset

`/settings` (linked from your name in the sidebar, or Settings on Home on a
phone) changes your name, email and password, and deletes the account with
everything in it. Each login is stored as `<user id>:<session_version>`;
changing or resetting the password raises `session_version`, which ends every
other login, "Keep me logged in" cookies included. User ids are never reused,
so nothing that names a deleted account can reach a new one.

"Forgot password?" emails a link that works for 1 hour and only once. The page
says the same thing whether or not the email has an account. Note that the
automatic daily backups (kept 14 days) still contain a deleted account until
they rotate out.

### Security protections (security.py)

- **CSRF:** every POST form carries a token (Flask-WTF). A missing or expired
  token shows "This page was open too long" (400). Write drafts survive in the browser.
- **Rate limits** (per IP, Flask-Limiter, in memory): log in 5/minute and 30/hour,
  sign up 5/hour. Counts are per server process and reset on restart.
- **Lockout:** 10 failed logins for one email within 15 minutes lock that email
  for 15 minutes (stored in the `login_throttle` table as a keyed hash, never the
  email). Unknown emails behave identically.
- **Headers:** a strict Content-Security-Policy with a per-request nonce, so every
  inline `<script>` needs `nonce="{{ csp_nonce }}"` and inline `on…=` handlers
  don't run. HSTS is sent in production.
- **Proxy:** in production the client IP comes from `X-Forwarded-For` (one proxy, as on Render).
- Failed logins and rate-limit hits are logged with the IP and a short email hash only.

### Upgrading a journal from before accounts

The upgrade runs by itself (or with `init-db`) and keeps every entry, but
entries written before accounts have no owner, so nobody sees them until they
are claimed. Sign up in the app, then run:

```sh
.venv/bin/python -m flask --app app assign-entries --email you@example.com
```

It gives every entry and tag without an owner to that account and prints how
many it moved. Entries that already have an owner are never touched, and it
is safe to run again.

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
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` | empty, `587` | SMTP (STARTTLS) for reset emails. Without `MAIL_SERVER`, or in development, emails are printed to the terminal |
| `TRUSTED_HOSTS` | empty | Production: the host name(s) the site is served at, comma-separated. Stops forged links in reset emails |

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
offers two downloads of your own entries: a readable Markdown file and a JSON file
with every column of every entry. There is no database download, because the
database file holds every user's journal. A JSON export can be restored on the
same page, into whichever account is logged in. Restoring only adds entries that
aren't already in that account (same `created_at` and `content`); it never
overwrites or deletes anything.

On the first request each day the app also copies the whole database to
`backups/journal-YYYY-MM-DD.db`, next to the database file, and keeps the newest 14.
These copies hold every user's journal, so keep that folder private. The page shows
when the last copy was made but not where it is. A failed backup is logged and
never stops a page from loading. The `backups/` folder is ignored by git.

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
