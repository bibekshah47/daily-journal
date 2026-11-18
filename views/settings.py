"""Account settings: name, email, password, and deleting the account.

Each section is a collapsed row with its own small form posting to its own
URL. A form with a problem shows the whole page again (status 400) with that
section open and the messages next to its fields; a successful one redirects
back with a toast and every section closed.
"""
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, logout_user

from accounts import (EmailTaken, change_display_name, change_email,
                      check_password, delete_user, display_name_error,
                      email_error, normalize_display_name, normalize_email,
                      password_changed_at, password_error, set_password)
from db import get_db
from security import (PASSWORD_CHECK_LIMITS, clear_failed_logins,
                      current_user_key, email_key, limiter)
from views.auth import User, start_session
from views.shared import draft_key, user_id

bp = Blueprint("settings", __name__)

WRONG_PASSWORD = "That password isn't right."
DELETE_WORD = "DELETE"

# Which collapsed section holds each field, and each form's URL.
SECTION_FOR_FIELD = {
    "display_name": "name",
    "new_email": "email", "email_password": "email",
    "current_password": "password", "new_password": "password",
    "delete_password": "delete", "delete_confirm": "delete",
}
SECTION_FOR_ENDPOINT = {
    "settings.change_name": "name", "settings.change_email_route": "email",
    "settings.change_password": "password", "settings.delete_account": "delete",
}


def render_settings(status=200, errors=None, values=None, general_error=None):
    """The settings page. `errors` maps field ids to messages; `values`
    keeps what was typed (never passwords). The section with a problem, or
    whose form was just refused (too many attempts), is rendered open."""
    connection = get_db()
    errors = errors or {}
    entry_count = connection.execute(
        "SELECT COUNT(*) FROM entries WHERE user_id = ?", (user_id(),)).fetchone()[0]
    shown = {"display_name": current_user.display_name or "", "new_email": "",
             "delete_confirm": ""}
    shown.update(values or {})
    open_section = next((SECTION_FOR_FIELD[field] for field in errors
                         if field in SECTION_FOR_FIELD), None)
    if open_section is None and status != 200:
        open_section = SECTION_FOR_ENDPOINT.get(request.endpoint)
    return render_template("settings.html", errors=errors, values=shown,
                           general_error=general_error, entry_count=entry_count,
                           open_section=open_section,
                           password_changed=password_changed_at(connection, user_id())), status


@bp.route("/settings")
def settings_page():
    return render_settings()


@bp.route("/settings/name", methods=["POST"])
def change_name():
    raw = request.form.get("display_name", "")
    name = normalize_display_name(raw)
    error = display_name_error(name)
    if error:
        return render_settings(400, {"display_name": error}, {"display_name": raw})
    change_display_name(get_db(), user_id(), name)
    flash("Name saved.")
    return redirect(url_for("settings.settings_page"))


@bp.route("/settings/email", methods=["POST"])
@limiter.limit(PASSWORD_CHECK_LIMITS, key_func=current_user_key, methods=["POST"])
def change_email_route():
    connection = get_db()
    email = normalize_email(request.form.get("new_email"))
    errors = {}
    problem = email_error(email)
    if problem is None and email == current_user.email:
        problem = "That's already your email"
    if problem:
        errors["new_email"] = problem
    if not check_password(connection, user_id(), request.form.get("email_password", "")):
        errors["email_password"] = WRONG_PASSWORD
    if not errors:
        try:
            change_email(connection, user_id(), email)
        except EmailTaken:
            errors["new_email"] = "An account with this email already exists"
    if errors:
        return render_settings(400, errors, {"new_email": email})
    flash("Email changed.")
    return redirect(url_for("settings.settings_page"))


@bp.route("/settings/password", methods=["POST"])
@limiter.limit(PASSWORD_CHECK_LIMITS, key_func=current_user_key, methods=["POST"])
def change_password():
    connection = get_db()
    new_password = request.form.get("new_password", "")
    errors = {}
    if not check_password(connection, user_id(), request.form.get("current_password", "")):
        errors["current_password"] = WRONG_PASSWORD
    problem = password_error(new_password)
    if problem:
        errors["new_password"] = problem
    if errors:
        return render_settings(400, errors)

    version = set_password(connection, user_id(), new_password)
    # Raising the version logged out every session, this one included: log
    # this device straight back in, keeping "Keep me logged in" if it was on.
    remember = current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token") in request.cookies
    start_session(User(current_user.id, current_user.email, current_user.display_name, version),
                  remember=remember)
    flash("Password changed. You've been logged out on your other devices.")
    return redirect(url_for("settings.settings_page"))


@bp.route("/settings/delete", methods=["POST"])
@limiter.limit(PASSWORD_CHECK_LIMITS, key_func=current_user_key, methods=["POST"])
def delete_account():
    connection = get_db()
    typed = request.form.get("delete_confirm", "").strip()
    errors = {}
    if not check_password(connection, user_id(), request.form.get("delete_password", "")):
        errors["delete_password"] = WRONG_PASSWORD
    if typed != DELETE_WORD:
        errors["delete_confirm"] = f"Type {DELETE_WORD} in capital letters to confirm"
    if errors:
        return render_settings(400, errors, {"delete_confirm": typed})

    old_draft_key = draft_key()
    old_email = current_user.email
    delete_user(connection, user_id())
    clear_failed_logins(connection, email_key(old_email))
    session.clear()
    logout_user()
    # The next page removes this user's unsaved Write draft from the browser.
    session["clear_draft_key"] = old_draft_key
    flash("Your account and journal were deleted.")
    return redirect(url_for("auth.login"))
