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
| `app.py` | Creates the app (`create_app()`), runs pending migrations at startup, registers the blueprints and CLI commands, runs the daily backup |
| `config.py` | Reads settings from environment variables and `.env` |
| `db.py` | `get_db()`: one database connection per request, closed automatically |
| `views/entries.py` | Home, Write, Edit, an entry, favorites, delete/restore, Trash |
| `views/history.py` | History calendar, search and filters |
| `views/insights.py` | Insights and the weekly review |
| `views/backup.py` | Backup & export and JSON restore |
| `views/auth.py` | Sign up, log in, log out, forgot/reset password, and the login check on every other page |
| `views/settings.py` | Settings: name, email, password, delete account |
| `accounts.py` | Email and password rules, password hashing, reset tokens, claiming old entries |
| `security.py` | CSRF, rate limits, login lockout, security headers |
| `mailer.py` | Sends email (printed to the terminal in development) |
| `logs.py`, `gunicorn.conf.py` | Production logging to stdout, with reset tokens redacted |
| `start.sh` | Render's start command: migrations, then gunicorn |
| `timeutil.py` | The user's clock (their time zone) and the system clock (UTC) |
| `database.py` | Connections to SQLite or PostgreSQL behind one API, and `?` placeholder conversion |
| `migrations.py` | Numbered schema migrations for both databases |
| `copy_to_postgres.py` | The `copy-sqlite-to-postgres` command |
| `backup.py`, `tags.py`, `memories.py`, `weekly.py`, `journal_insights.py`, `trash.py` | Logic and SQL; each function takes a connection and the user's id |

Routes get their connection from `get_db()` and pass it to the helper modules,
which never open their own. The only exceptions are migrations (`migrations.py`),
the SQLite file copies made for daily backups (`backup.py`), and the copy command.
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

### Time zones

The server can run on UTC, but a journal's "today" is the writer's day. Each
user has a time zone (`users.timezone`): sign up and log in send the
browser's zone in a hidden field, and Settings has a **Time zone** row to
change it (with "Use this device's time zone"). Logging in only fills in a
missing or `UTC` zone, so a zone chosen in Settings isn't replaced while
traveling. Unknown zones fall back to `UTC`; accounts from before time zones
were set to `America/New_York`, the time their entries were written in. Old
zone names that some browsers still report (Chrome says `Asia/Calcutta`) are
stored by their current name (`Asia/Kolkata`), using tzdata's list of aliases.

`timeutil.py` holds both clocks. `user_now()` and `user_today()` (the
greeting, today's entry, the 14-day strip, streaks, Insights, the week,
On this day, History's today, new entries, Trash dates, export names) give
the user's wall-clock time. `utc_now()` serves system work: login lockouts,
account `created_at`, migrations, the daily SQLite backup, and the Trash
cleanup, which allows one extra day so time zones behind UTC never lose an
entry early. Timestamps stay naive ISO text, as before. Only `timeutil.clock()`
reads the real time, so tests freeze it with `helpers.freeze_time`, and the test
run uses `TZ=UTC` like a production server.

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

The upgrade runs by itself when the app starts (or with `init-db`) and keeps every entry, but
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
| `DATABASE` | `journal.db` | Path to the SQLite file (local development). The older `JOURNAL_DATABASE` name still works |
| `DATABASE_URL` | empty | A PostgreSQL connection string (`postgres://` or `postgresql://`). When set, it is used instead of `DATABASE`. Required in production |
| `TEST_DATABASE_URL` | empty | Optional: a throwaway PostgreSQL database for running the tests against PostgreSQL |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` | empty, `587` | SMTP (STARTTLS) for reset emails. In development, emails are printed to the terminal instead. In production without `MAIL_SERVER` they aren't sent (or printed: they hold reset links) |
| `TRUSTED_HOSTS` | empty | Production: the host name(s) the site is served at, comma-separated. Other hosts get a 400 (except `/healthz`), which stops forged links in reset emails |

Variables set in your shell take priority over `.env`. In production
(`APP_ENV=production`) the app refuses to start unless `SECRET_KEY` is set to a
real secret and `DATABASE_URL` points at PostgreSQL. Generate a secret with:

```sh
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```

## Databases: SQLite locally, PostgreSQL in production

With no `DATABASE_URL`, the app uses the SQLite file at `DATABASE` (`journal.db`).
That is all local development and the default test run need.

In production, set `DATABASE_URL` to a PostgreSQL connection string. For
[Neon](https://neon.tech): open your project, click **Connect**, and copy the
connection string. Put it in your host's environment settings (on Render: the
service's **Environment** tab), never in `.env.example` or anything else git tracks:

```
DATABASE_URL=postgresql://USER:PASSWORD@ep-example-123456-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
```

Neon's pooled (`-pooler`) string is fine for the app. To try PostgreSQL locally,
put the same line in `.env`.

The app is written once for both databases. `database.py` gives every module
the same small API (`execute`, `fetchone`, `fetchall`, `insert` returning the new
id) and turns `?` placeholders into `%s` for PostgreSQL. SQL stays portable:
`INSERT ... RETURNING id` for new ids, `substr(created_at, 1, 10)` for the day
of an ISO timestamp, `LOWER(col) LIKE LOWER(?)` for searching, and
`ON CONFLICT DO NOTHING` instead of `INSERT OR IGNORE`. Booleans are `0`/`1` integers.

### Schema and migrations

`migrations.py` holds numbered migrations, each with SQL for SQLite and for
PostgreSQL, and records the ones applied in a `schema_migrations` table. Pending
migrations run when the app starts (never per request), and on demand:

```sh
.venv/bin/python -m flask --app app init-db
```

It works on whichever database is configured and is safe to run any number of times.
To change the schema, add a migration with the next number; never edit one that
has shipped. A SQLite `journal.db` from before migrations is recognized
automatically: it is brought up to date if it is an older layout, then recorded
as version 1, keeping every row.

### Moving your SQLite journal to PostgreSQL

With `DATABASE_URL` pointing at an empty PostgreSQL database:

```sh
DATABASE_URL='postgresql://...' .venv/bin/python -m flask --app app copy-sqlite-to-postgres --sqlite journal.db
```

This copies users, entries, tags and tag links in one transaction, keeping every
id, then moves each id sequence past the highest copied id. It prints how many
rows it copied, refuses a database that already has users or entries, and never
changes the SQLite file. Logins and "Keep me logged in" cookies keep working,
because user ids are unchanged.

## Running the app

Stop any running development server first, then:

```sh
.venv/bin/python app.py
```

The server runs at http://127.0.0.1:5001, with Flask's debugger on in development
and off in production. `.venv/bin/python -m flask --app app run --port 5001` works
too. Flask's built-in server is for local use only. To deploy, set `APP_ENV=production`,
`SECRET_KEY` and `DATABASE_URL`, then serve `app:app` with a WSGI server such as gunicorn.

`content` stays plain text for previews and search. `content_html` holds sanitized
formatting and is NULL on legacy entries. Legacy content is never interpreted as
HTML. Sanitization runs on submission and when rendering formatted content.

## Deploying to Render

Create a **Web Service** from this repository (Render reads the Python version
from `.python-version`) with:

| Setting | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` |
| Start command | `./start.sh` |
| Health check path | `/healthz` |

`start.sh` runs `flask --app app init-db` (it only applies pending migrations,
so it is safe on every deploy), then starts gunicorn on Render's `$PORT` with one
worker and four threads. One worker, because the rate limits are counted in
memory: several workers would each keep their own counts. `/healthz` answers
`ok` after a `SELECT 1` on the database, or 503 if the database can't be
reached; it needs no login and skips the HTTPS, host and rate-limit checks so
Render's health checker can always reach it.

Environment variables (the service's **Environment** tab):

| Variable | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `SECRET_KEY` | A long random value: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | Your PostgreSQL connection string (Neon: **Connect**, then copy it) |
| `TRUSTED_HOSTS` | The host name(s) the site is served at, comma-separated, e.g. `my-journal.onrender.com` |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` | Optional for now. Without `MAIL_SERVER`, password reset emails aren't sent, and a warning is logged at startup |

In production the app trusts one proxy hop (Render's) for the visitor's IP and
for HTTPS, so `request.is_secure` is true and links in emails are `https://`.
Plain-HTTP requests are redirected to HTTPS (301 for GET, 308 for forms), and
with `TRUSTED_HOSTS` set any other `Host` header gets a 400. Logs go to stdout
at INFO, where Render shows them. They never include passwords, entry text or
full email addresses (only a short hash), and password reset tokens are replaced
with `[redacted]` in app logs and in gunicorn's access log (`gunicorn.conf.py`,
which gunicorn loads by itself).

To move an existing local journal to the new database, see
[Moving your SQLite journal to PostgreSQL](#moving-your-sqlite-journal-to-postgresql).

## Backup & export

The **Backup & export** link at the bottom of the sidebar opens `/backup`, which
offers two downloads of your own entries: a readable Markdown file and a JSON file
with every column of every entry. There is no database download, because the
database file holds every user's journal. A JSON export can be restored on the
same page, into whichever account is logged in. Restoring only adds entries that
aren't already in that account (same `created_at` and `content`); it never
overwrites or deletes anything.

Markdown and JSON export and JSON restore work the same on SQLite and PostgreSQL.

With SQLite, on the first request each day the app also copies the whole database to
`backups/journal-YYYY-MM-DD.db`, next to the database file, and keeps the newest 14.
These copies hold every user's journal, so keep that folder private. The page shows
when the last copy was made but not where it is. A failed backup is logged and
never stops a page from loading. The `backups/` folder is ignored by git.

With PostgreSQL there are no file copies and the page doesn't mention them: use
your database host's restore history instead (Neon keeps point-in-time restore
history for your project).

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

Python tests create a temporary SQLite database for each test and verify the real
database checksum is unchanged. They ignore `DATABASE_URL`, so they never touch a
real database. JavaScript tests use DOM/editor doubles for integration logic; real
browser toolbar and visual checks are covered by the manual steps above.

### Running the tests against PostgreSQL

Point `TEST_DATABASE_URL` (in your shell or `.env`) at a PostgreSQL database
you don't mind the tests writing to, such as a separate Neon branch. Use its
**direct** connection string (without `-pooler`): each test creates its own
schema, sets `search_path` to it, and drops it afterwards, which needs a direct
connection. Then:

```sh
TEST_DB=postgres .venv/bin/python -m unittest discover -s tests
```

Every test runs on PostgreSQL, apart from the few about SQLite itself (file
backups, upgrading old journal files), which are skipped. The tests for
`copy-sqlite-to-postgres` only run in this mode. It is slower than the SQLite run,
since every test makes its own connections and schema.
