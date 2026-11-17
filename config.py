"""Settings from environment variables, with safe defaults for local use.

A .env file next to this one is loaded first when python-dotenv is installed.
Variables already set in the environment win over the file. See .env.example.
"""
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


class ConfigError(RuntimeError):
    """The environment is not safe or not valid for starting the app."""


def database_path(environ=None):
    """The SQLite file: DATABASE, or the older JOURNAL_DATABASE, or journal.db."""
    environ = os.environ if environ is None else environ
    return (environ.get("DATABASE", "").strip()
            or environ.get("JOURNAL_DATABASE", "").strip()
            or DEFAULT_DATABASE)


def load_config(environ=None):
    """Flask config values for the current environment.

    Raises ConfigError in production when SECRET_KEY is missing or is the
    development default, so a deployment can't run with a guessable key.
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

    return {
        "APP_ENV": app_env,
        "SECRET_KEY": secret_key or DEFAULT_SECRET_KEY,
        "DATABASE": database_path(environ),
        "MAX_CONTENT_LENGTH": MAX_UPLOAD_MB * 1024 * 1024,
    }
