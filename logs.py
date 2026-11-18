"""Logging for production, and keeping secrets out of every log.

In production (APP_ENV=production) the app logs to stdout at INFO, which is
where Render collects logs. The app never logs passwords, entry content or
full email addresses (only a short keyed hash). Password reset tokens travel
in URLs (/reset/<token>), so `redact()` removes them from log lines; the app
logger and gunicorn's access log (gunicorn.conf.py) both use it.
"""
import logging
import re
import sys

_RESET_TOKEN = re.compile(r"(/reset/)[^/?#\s\"']+")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def redact(text):
    """`text` with any password reset token replaced by [redacted]."""
    if not isinstance(text, str):
        return text
    return _RESET_TOKEN.sub(r"\1[redacted]", text)


class RedactFilter(logging.Filter):
    """Redacts reset tokens from a record's final message."""

    def filter(self, record):
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


class _StdoutHandler(logging.StreamHandler):
    """Marks the handler this module added, so it is only added once."""


def configure_logging(app):
    """Production: the app logger writes INFO and above to stdout."""
    if app.config["APP_ENV"] != "production":
        return
    logger = app.logger
    if not any(isinstance(handler, _StdoutHandler) for handler in logger.handlers):
        from flask.logging import default_handler
        logger.removeHandler(default_handler)   # Flask's goes to stderr
        handler = _StdoutHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
