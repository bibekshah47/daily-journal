"""Sign up, log in and log out. Every other page needs a logged-in user."""
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_user,
                         logout_user)

from accounts import (EmailTaken, authenticate, create_user, display_name_error,
                      email_error, find_user, normalize_display_name,
                      normalize_email, password_error)
from db import get_db
from security import (FAILED_LOGIN_MESSAGE, LOGIN_LIMITS, SIGNUP_LIMITS,
                      TOO_MANY_MESSAGE, clear_failed_logins, client_ip,
                      email_key, limiter, log_label, login_locked,
                      record_failed_login)
from views.shared import safe_path

bp = Blueprint("auth", __name__)

login_manager = LoginManager()
# A session cookie used from a different IP or browser is thrown away.
login_manager.session_protection = "strong"

# Pages anyone can open. Everything else redirects to /login. Unknown URLs
# (no endpoint) get the normal 404 page.
PUBLIC_ENDPOINTS = {"auth.login", "auth.signup", "auth.logout", "static"}


class User(UserMixin):
    def __init__(self, user_id, email, display_name=None):
        self.id = user_id
        self.email = email
        # Plain text from the user: templates show it autoescaped, never |safe.
        self.display_name = display_name


@login_manager.user_loader
def load_user(user_id):
    try:
        row = find_user(get_db(), int(user_id))
    except (TypeError, ValueError):
        return None
    return User(row[0], row[1], row[2]) if row else None


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
                new_id = create_user(connection, email, password, display_name)
                connection.commit()
            except EmailTaken:
                connection.rollback()
                errors["email_taken"] = True
            else:
                start_session(User(new_id, email, display_name))
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
            start_session(User(*user), remember=request.form.get("remember") == "1")
            return redirect(next_path or url_for("entries.home"))

    return render_template("login.html", email=email, error=error,
                           next_path=next_path), 400 if error else 200


@bp.route("/logout", methods=["POST"])
def logout():
    # Clear first: logout_user() then marks the remember cookie for deletion.
    session.clear()
    logout_user()
    flash("Logged out.")
    return redirect(url_for("auth.login"))
