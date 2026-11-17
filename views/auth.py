"""Sign up, log in and log out. Every other page needs a logged-in user."""
from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_user,
                         logout_user)

from accounts import (EmailTaken, authenticate, create_user, email_error,
                      find_user, normalize_email, password_error)
from db import get_db
from views.shared import safe_path

bp = Blueprint("auth", __name__)

login_manager = LoginManager()

# Pages anyone can open. Everything else redirects to /login. Unknown URLs
# (no endpoint) get the normal 404 page.
PUBLIC_ENDPOINTS = {"auth.login", "auth.signup", "auth.logout", "static"}


class User(UserMixin):
    def __init__(self, user_id, email):
        self.id = user_id
        self.email = email


@login_manager.user_loader
def load_user(user_id):
    try:
        row = find_user(get_db(), int(user_id))
    except (TypeError, ValueError):
        return None
    return User(row[0], row[1]) if row else None


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


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("entries.home"))

    email = ""
    errors = {}
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        errors = {"email": email_error(email), "password": password_error(password)}
        errors = {field: message for field, message in errors.items() if message}
        if not errors:
            connection = get_db()
            try:
                new_id = create_user(connection, email, password)
                connection.commit()
            except EmailTaken:
                connection.rollback()
                errors["email_taken"] = True
            else:
                login_user(User(new_id, email))
                flash("Welcome! Your journal is ready.")
                return redirect(url_for("entries.home"))

    return render_template("signup.html", email=email, errors=errors), 400 if errors else 200


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_path = safe_path(request.values.get("next", ""))
    if current_user.is_authenticated:
        return redirect(next_path or url_for("entries.home"))

    email = ""
    error = None
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        user = authenticate(get_db(), email, request.form.get("password", ""))
        if user is None:
            error = "Email or password is incorrect."
        else:
            login_user(User(*user), remember=request.form.get("remember") == "1")
            return redirect(next_path or url_for("entries.home"))

    return render_template("login.html", email=email, error=error,
                           next_path=next_path), 400 if error else 200


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("Logged out.")
    return redirect(url_for("auth.login"))
