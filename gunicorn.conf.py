"""Gunicorn settings that start.sh's command line doesn't set.

Gunicorn reads ./gunicorn.conf.py automatically. The access log (written to
stdout with --access-logfile -) records each request line and its Referer,
and a password reset link carries its token in the URL (/reset/<token>):
this logger replaces the token with [redacted] before anything is written.
"""
from gunicorn.glogging import Logger

from logs import redact


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        # Every value: the request line (r), path (U), query (q), Referer (f)
        # and any header a custom log format might add.
        atoms = super().atoms(resp, req, environ, request_time)
        return {key: redact(value) for key, value in atoms.items()}


logger_class = RedactingLogger
