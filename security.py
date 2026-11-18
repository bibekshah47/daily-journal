"""Protection for a public deployment: CSRF tokens, login rate limits,
a per-email lockout, and security headers with a per-request CSP nonce.

Wired up by init_app() in app.py. The login and signup views use `limiter`,
`email_key`, `login_locked`, `record_failed_login` and `clear_failed_logins`.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets

from flask import current_app, g, render_template, request
from flask_limiter import Limiter, RateLimitExceeded
from flask_limiter.util import get_remote_address
from flask_login import user_loaded_from_cookie
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

csrf = CSRFProtect()

# Per client IP. In memory, so each server process counts separately and a
# restart resets the counts; good enough for one small instance.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")

LOGIN_LIMITS = "5 per minute;30 per hour"
SIGNUP_LIMITS = "5 per hour"

# After this many failed logins for one email within LOCKOUT_WINDOW,
# that email can't log in for LOCKOUT_WINDOW.
MAX_FAILED_LOGINS = 10
LOCKOUT_WINDOW = timedelta(minutes=15)

FAILED_LOGIN_MESSAGE = "Email or password is incorrect."
TOO_MANY_MESSAGE = "Too many attempts. Try again in a few minutes."

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "object-src 'none'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

HSTS = "max-age=31536000; includeSubDomains"


# ---------- Identifying the client without storing personal data ----------

def email_key(email):
    """A keyed hash of an (already normalized) email: the same email always
    gives the same key, but the key can't be turned back into the email."""
    secret = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, email.encode("utf-8"), hashlib.sha256).hexdigest()


def log_label(email):
    """Short email hash for log lines. Never log the email or password."""
    return email_key(email)[:16]


def client_ip():
    return request.remote_addr or "unknown"


# ---------- Per-email lockout ----------

def _parse(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def login_locked(connection, key, now=None):
    """True while this email is locked out after too many failed logins."""
    row = connection.execute(
        "SELECT locked_until FROM login_throttle WHERE email_hash = ?", (key,)).fetchone()
    locked_until = _parse(row[0]) if row else None
    return locked_until is not None and locked_until > (now or datetime.now())


def record_failed_login(connection, key, now=None):
    """Count one failed login. Returns True if this failure locked the email. Commits."""
    now = now or datetime.now()
    window_cutoff = now - LOCKOUT_WINDOW
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        # Forget emails whose lockout and counting window are both over.
        connection.execute(
            "DELETE FROM login_throttle WHERE (locked_until IS NULL OR locked_until < ?) "
            "AND (window_start IS NULL OR window_start < ?)",
            (now.isoformat(), window_cutoff.isoformat()))
        row = connection.execute(
            "SELECT failures, window_start FROM login_throttle WHERE email_hash = ?",
            (key,)).fetchone()
        window_start = _parse(row[1]) if row else None
        if window_start is None or window_start < window_cutoff:
            failures, window_start = 1, now
        else:
            failures = row[0] + 1
        locked = failures >= MAX_FAILED_LOGINS
        if locked:
            values = (0, None, (now + LOCKOUT_WINDOW).isoformat())
        else:
            values = (failures, window_start.isoformat(), None)
        connection.execute(
            "INSERT INTO login_throttle (email_hash, failures, window_start, locked_until) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(email_hash) DO UPDATE SET "
            "failures = excluded.failures, window_start = excluded.window_start, "
            "locked_until = COALESCE(excluded.locked_until, locked_until)",
            (key, *values))
    return locked


def clear_failed_logins(connection, key):
    """A successful login starts the count again. Commits."""
    with connection:
        connection.execute("DELETE FROM login_throttle WHERE email_hash = ?", (key,))


# ---------- CSP nonce and headers ----------

def csp_nonce():
    """This request's random nonce; every inline <script> must carry it."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


def set_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY.format(nonce=csp_nonce())
    if current_app.config["APP_ENV"] == "production":
        response.headers["Strict-Transport-Security"] = HSTS
    return response


# ---------- Error pages ----------

def csrf_failed(error):
    """A form posted with a missing or expired CSRF token."""
    return render_template("csrf_error.html"), 400


def too_many_requests(error):
    """Show the form again with a friendly message instead of a raw 429."""
    from accounts import normalize_email   # avoid an import cycle at startup
    from views.shared import safe_path

    email = normalize_email(request.form.get("email"))
    current_app.logger.warning("Rate limit hit on %s from %s (email hash %s)",
                               request.path, client_ip(), log_label(email))
    if request.endpoint == "auth.signup":
        return render_template("signup.html", email=email, display_name="",
                               errors={"too_many": TOO_MANY_MESSAGE}), 429
    return render_template("login.html", email=email,
                           error=f"{FAILED_LOGIN_MESSAGE} {TOO_MANY_MESSAGE}",
                           next_path=safe_path(request.values.get("next", ""))), 429


# ---------- Wiring ----------

def _ratelimit_disabled():
    """Read on every request, so tests can switch limits off after startup."""
    return not current_app.config.get("RATELIMIT_ENABLED", True)


def _remember_session_identifier(sender, user):
    """Flask-Login's "strong" protection compares the session's _id with the
    client on every request, but a login from the remember cookie doesn't set
    _id, so the very next request would log the user out again."""
    from flask import session
    session["_id"] = sender.login_manager._session_identifier_generator()


def init_app(app):
    if app.config["APP_ENV"] == "production":
        # Render runs the app behind one proxy that sets X-Forwarded-For.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

    # The limiter's check runs before CSRF's, so posts without a token are
    # still counted.
    limiter.init_app(app)
    # The limiter object is shared by every app create_app() builds (tests
    # build several), and init_app copies RATELIMIT_ENABLED onto it. One app
    # with limits off must not switch them off for the others: each app's
    # setting is applied per request by _ratelimit_disabled instead.
    limiter.enabled = True
    csrf.init_app(app)

    app.after_request(set_security_headers)
    app.context_processor(lambda: {"csp_nonce": csp_nonce()})
    app.register_error_handler(CSRFError, csrf_failed)
    app.register_error_handler(RateLimitExceeded, too_many_requests)
    user_loaded_from_cookie.connect(_remember_session_identifier, app)


limiter.request_filter(_ratelimit_disabled)
