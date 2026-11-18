"""Settings from environment variables, with safe defaults for local use.

A .env file next to this one is loaded first when python-dotenv is installed.
Variables already set in the environment win over the file. See .env.example.
"""
from datetime import timedelta
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:   # optional: plain environment variables still work
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(Path(__file__).with_name(".env"))

ENVIRONMENTS = ("development", "production")

# Only good enough for running the app on your own computer.
DEFAULT_SECRET_KEY = "dev-only-insecure-secret-key"

DEFAULT_DATABASE = "journal.db"

# Largest upload accepted (the JSON restore file).
MAX_UPLOAD_MB = 10

# Outgoing mail (password reset links). STARTTLS on this port by default.
DEFAULT_MAIL_PORT = 587

# How long "Keep me logged in" lasts.
REMEMBER_DAYS = 30


class ConfigError(RuntimeError):
    """The environment is not safe or not valid for starting the app."""


def database_path(environ=None):
    """The SQLite file: DATABASE, or the older JOURNAL_DATABASE, or journal.db."""
    environ = os.environ if environ is None else environ
    return (environ.get("DATABASE", "").strip()
            or environ.get("JOURNAL_DATABASE", "").strip()
            or DEFAULT_DATABASE)


POSTGRES_SCHEMES = ("postgres://", "postgresql://")


def database_target(environ=None, production=False):
    """Where the data lives: the PostgreSQL DATABASE_URL if set, else the
    SQLite file path. Production must use PostgreSQL."""
    environ = os.environ if environ is None else environ
    url = environ.get("DATABASE_URL", "").strip()
    if url:
        if not url.startswith(POSTGRES_SCHEMES):
            raise ConfigError("DATABASE_URL must start with postgres:// or postgresql://.")
        return url
    if production:
        raise ConfigError(
            "DATABASE_URL must be set in production: the PostgreSQL connection "
            "string from your database host (for example Neon).")
    return database_path(environ)


def load_config(environ=None):
    """Flask config values for the current environment.

    Raises ConfigError in production when SECRET_KEY is missing or is the
    development default, so a deployment can't run with a guessable key, or
    when DATABASE_URL (PostgreSQL) is missing.
    """
    environ = os.environ if environ is None else environ
    app_env = environ.get("APP_ENV", "").strip().lower() or "development"
    if app_env not in ENVIRONMENTS:
        raise ConfigError(
            f"APP_ENV must be one of {', '.join(ENVIRONMENTS)}, not {app_env!r}.")

    secret_key = environ.get("SECRET_KEY", "").strip()
    if app_env == "production" and secret_key in ("", DEFAULT_SECRET_KEY):
        raise ConfigError(
            "SECRET_KEY must be set to a long random value in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")

    port = environ.get("MAIL_PORT", "").strip() or str(DEFAULT_MAIL_PORT)
    if not port.isdigit():
        raise ConfigError(f"MAIL_PORT must be a number, not {port!r}.")
    # Production: the hosts this app answers to, comma-separated
    # ("journal.example.com"). Links in emails use the request's host, so this
    # stops a forged Host header from putting someone else's domain in a
    # password reset email. Checked by security.py (not Flask's own
    # TRUSTED_HOSTS, which would also refuse Render's health checks).
    trusted_hosts = [host.strip() for host in environ.get("TRUSTED_HOSTS", "").split(",")
                     if host.strip()]

    production = app_env == "production"
    settings = {
        "APP_ENV": app_env,
        "SECRET_KEY": secret_key or DEFAULT_SECRET_KEY,
        # A SQLite file path, or a PostgreSQL URL (see database.py).
        "DATABASE": database_target(environ, production),
        "MAX_CONTENT_LENGTH": MAX_UPLOAD_MB * 1024 * 1024,
        # Login cookies: never readable by page scripts, never sent with
        # cross-site form posts, and HTTPS-only in production.
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": production,
        "REMEMBER_COOKIE_HTTPONLY": True,
        "REMEMBER_COOKIE_SAMESITE": "Lax",
        "REMEMBER_COOKIE_SECURE": production,
        "REMEMBER_COOKIE_DURATION": timedelta(days=REMEMBER_DAYS),
        "PREFERRED_URL_SCHEME": "https" if production else "http",
        "MAIL_SERVER": environ.get("MAIL_SERVER", "").strip(),
        "MAIL_PORT": int(port),
        "MAIL_USERNAME": environ.get("MAIL_USERNAME", "").strip(),
        "MAIL_PASSWORD": environ.get("MAIL_PASSWORD", ""),
        "MAIL_FROM": environ.get("MAIL_FROM", "").strip(),
        "ALLOWED_HOSTS": trusted_hosts,
    }
    if production:
        # Never run the interactive debugger on a public server, even if
        # FLASK_DEBUG is set. In development Flask reads FLASK_DEBUG itself.
        settings["DEBUG"] = False
    return settings
