"""Sign up, log in, log out and password reset. Every other page needs a
logged-in user."""
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_user,
                         logout_user)

from accounts import (RESET_MAX_AGE, EmailTaken, adopt_browser_timezone,
                      authenticate, create_user,
                      display_name_error, email_error, find_user,
                      find_user_by_email, make_reset_token,
                      normalize_display_name, normalize_email, password_error,
                      set_password, user_for_reset_token)
from db import get_db
from mailer import send_email
from timeutil import normalize_timezone
from security import (FAILED_LOGIN_MESSAGE, FORGOT_LIMITS, LOGIN_LIMITS,
                      SIGNUP_LIMITS, TOO_MANY_MESSAGE, clear_failed_logins,
                      client_ip, email_key, forgot_email_allowed, limiter,
                      log_label, login_locked, record_failed_login)
from views.shared import safe_path

bp = Blueprint("auth", __name__)

login_manager = LoginManager()
# A session cookie used from a different IP or browser is thrown away.
login_manager.session_protection = "strong"

# Pages anyone can open. Everything else redirects to /login. Unknown URLs
# (no endpoint) get the normal 404 page.
PUBLIC_ENDPOINTS = {"auth.login", "auth.signup", "auth.logout", "auth.forgot_password",
                    "auth.reset_password", "static", "healthz"}

FORGOT_MESSAGE = ("If an account exists for that email, we've sent a link to reset "
                  "your password. It works for 1 hour.")


class User(UserMixin):
    """Built from accounts.USER_COLUMNS: id, email, display_name,
    session_version, timezone."""

    def __init__(self, user_id, email, display_name=None, session_version=0, timezone=None):
        self.id = user_id
        self.email = email
        # Plain text from the user: templates show it autoescaped, never |safe.
        self.display_name = display_name
        self.session_version = session_version
        # Validated when stored; timeutil treats missing or invalid as UTC.
        self.timezone = timezone

    def get_id(self):
        """What Flask-Login stores in the session and remember cookie.

        Includes session_version, so raising the version in the database
        (password change or reset) ends every existing login at once.
        """
        return f"{self.id}:{self.session_version}"


def parse_login_id(value):
    """(user id, session_version) from "<id>:<version>", or None.

    A bare "<id>" (sessions from before versions existed) means version 0,
    so it stops working as soon as the version is first raised.
    """
    user_id, _, version = str(value or "").partition(":")
    version = version or "0"
    if not (user_id.isdigit() and version.isdigit()):
        return None
    return int(user_id), int(version)


@login_manager.user_loader
def load_user(login_id):
    parsed = parse_login_id(login_id)
    if parsed is None:
        return None
    row = find_user(get_db(), parsed[0])
    if row is None or row[3] != parsed[1]:
        return None
    return User(*row)


def login_redirect():
    """Send a logged-out visitor to /login, remembering the page they wanted.

    Only GET requests are remembered: going back to a form's URL after
    logging in would not resubmit the form anyway.
    """
    target = None
    if request.method in ("GET", "HEAD"):
        target = request.path
        if request.query_string:
            target += "?" + request.query_string.decode("utf-8", "replace")
        target = safe_path(target)
    return redirect(url_for("auth.login", next=target))


login_manager.unauthorized_handler(login_redirect)


def require_login():
    """Runs before every request: the whole app is private except PUBLIC_ENDPOINTS."""
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not current_user.is_authenticated:
        return login_redirect()
    return None


def init_app(app):
    login_manager.init_app(app)
    app.before_request(require_login)


def start_session(user, remember=False):
    """Log `user` in with a brand-new session, so nothing set before login
    (such as a session id planted by someone else) carries over."""
    session.clear()
    login_user(user, remember=remember)


@bp.route("/signup", methods=["GET", "POST"])
@limiter.limit(SIGNUP_LIMITS, methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("entries.home"))

    email = ""
    display_name = None
    errors = {}
    if request.method == "POST":
        display_name = normalize_display_name(request.form.get("display_name"))
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        errors = {"display_name": display_name_error(display_name),
                  "email": email_error(email), "password": password_error(password)}
        errors = {field: message for field, message in errors.items() if message}
        if not errors:
            connection = get_db()
            try:
                # The browser's time zone, from the form's hidden field.
                timezone = normalize_timezone(request.form.get("timezone"))
                new_id = create_user(connection, email, password, display_name, timezone)
                connection.commit()
            except EmailTaken:
                connection.rollback()
                errors["email_taken"] = True
            else:
                start_session(User(new_id, email, display_name, 0, timezone))
                flash("Welcome! Your journal is ready.")
                return redirect(url_for("entries.home"))

    return render_template("signup.html", email=email, display_name=display_name or "",
                           errors=errors), 400 if errors else 200


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMITS, methods=["POST"])
def login():
    next_path = safe_path(request.values.get("next", ""))
    if current_user.is_authenticated:
        return redirect(next_path or url_for("entries.home"))

    email = ""
    error = None
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        connection = get_db()
        key = email_key(email)
        # Locked or not, unknown emails behave exactly like real ones.
        if login_locked(connection, key):
            current_app.logger.warning("Login refused (email locked) from %s (email hash %s)",
                                       client_ip(), log_label(email))
            return render_template("login.html", email=email,
                                   error=f"{FAILED_LOGIN_MESSAGE} {TOO_MANY_MESSAGE}",
                                   next_path=next_path), 429
        user = authenticate(connection, email, request.form.get("password", ""))
        if user is None:
            record_failed_login(connection, key)
            current_app.logger.warning("Failed login from %s (email hash %s)",
                                       client_ip(), log_label(email))
            error = FAILED_LOGIN_MESSAGE
        else:
            clear_failed_logins(connection, key)
            user_id, user_email, display_name, session_version, stored_zone = user
            timezone = adopt_browser_timezone(connection, user_id, stored_zone,
                                              request.form.get("timezone"))
            start_session(User(user_id, user_email, display_name, session_version, timezone),
                          remember=request.form.get("remember") == "1")
            return redirect(next_path or url_for("entries.home"))

    return render_template("login.html", email=email, error=error,
                           next_path=next_path), 400 if error else 200


@bp.route("/forgot", methods=["GET", "POST"])
@limiter.limit(FORGOT_LIMITS, methods=["POST"])
def forgot_password():
    """Email a reset link. The page looks the same whether or not the email
    has an account, and so does the time it takes (sending is in the background)."""
    email = ""
    problem = None
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        problem = email_error(email)
        if problem is None:
            if not forgot_email_allowed(email):
                current_app.logger.warning("Reset requests limited for email hash %s (from %s)",
                                           log_label(email), client_ip())
            else:
                user = find_user_by_email(get_db(), email)
                if user is not None:
                    send_reset_email(user)
            return render_template("forgot.html", email=email, sent=True,
                                   message=FORGOT_MESSAGE)
    return render_template("forgot.html", email=email, sent=False,
                           email_problem=problem), 400 if problem else 200


def send_reset_email(user):
    user_id, email, display_name, session_version, _ = user
    token = make_reset_token(current_app.config["SECRET_KEY"], user_id, session_version)
    # https in production even behind a proxy that talks to the app over http.
    link = url_for("auth.reset_password", token=token, _external=True,
                   _scheme=current_app.config["PREFERRED_URL_SCHEME"])
    greeting = f"Hi {display_name.split()[0]}," if display_name else "Hi,"
    body = (f"{greeting}\n\n"
            "Someone asked to reset the password for your My Journal account.\n"
            f"To choose a new password, open this link within {RESET_MAX_AGE // 3600} hour:\n\n"
            f"{link}\n\n"
            "If you didn't ask for this, you can ignore this email. "
            "Your password won't change.\n\n"
            "My Journal\n")
    send_email(email, "Reset your My Journal password", body)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    connection = get_db()
    user = user_for_reset_token(connection, current_app.config["SECRET_KEY"], token)
    if user is None:
        return render_template("reset.html", expired=True), 400

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        error = password_error(password)
        if error is None:
            version = set_password(connection, user[0], password)
            clear_failed_logins(connection, email_key(user[1]))
            start_session(User(user[0], user[1], user[2], version, user[4]))
            flash("Password updated.")
            return redirect(url_for("entries.home"))
    return render_template("reset.html", expired=False, error=error), 400 if error else 200


@bp.route("/logout", methods=["POST"])
def logout():
    # Clear first: logout_user() then marks the remember cookie for deletion.
    session.clear()
    logout_user()
    flash("Logged out.")
    return redirect(url_for("auth.login"))
