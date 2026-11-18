"""Sending email (password reset links).

In development, emails are printed to the terminal instead of sent. In
production they go out over SMTP with STARTTLS, using MAIL_SERVER, MAIL_PORT,
MAIL_USERNAME, MAIL_PASSWORD and MAIL_FROM; without MAIL_SERVER they are not
sent (and never printed: a reset email holds a secret link), only logged as
not sent.

Sending never breaks a request: real sends happen on a background thread
(so a slow mail server doesn't slow the page down, and response times don't
reveal whether an email was sent) and failures are only logged.

Tests can set config MAIL_OUTBOX to a list: messages are appended there.
"""
from email.message import EmailMessage
from email.utils import formataddr
import smtplib
import ssl
import threading

from flask import current_app

FROM_NAME = "My Journal"
SMTP_TIMEOUT_SECONDS = 15


def send_email(to, subject, text_body):
    """Send (or print) a plain-text email. Returns the sending thread, if any."""
    app = current_app._get_current_object()
    config = app.config
    try:
        outbox = config.get("MAIL_OUTBOX")
        if outbox is not None:
            outbox.append({"to": to, "subject": subject, "body": text_body})
            return None

        if config["APP_ENV"] != "development" and not config.get("MAIL_SERVER"):
            # Never print it in production: the body holds a reset link.
            app.logger.warning("MAIL_SERVER is not set, so an email (%s) was not sent.", subject)
            return None
        if config["APP_ENV"] == "development":
            print(f"\n----- Email (not sent) -----\nTo: {to}\nSubject: {subject}\n\n"
                  f"{text_body}\n----------------------------\n", flush=True)
            return None

        message = EmailMessage()
        message["From"] = formataddr((FROM_NAME, config.get("MAIL_FROM") or config.get("MAIL_USERNAME")))
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text_body)
    except Exception:
        app.logger.exception("Could not prepare an email (%s)", subject)
        return None

    thread = threading.Thread(target=deliver, args=(app, message), daemon=True)
    thread.start()
    return thread


def deliver(app, message):
    """Hand one message to the SMTP server. Logs (never raises) on failure."""
    config = app.config
    try:
        with smtplib.SMTP(config["MAIL_SERVER"], config.get("MAIL_PORT", 587),
                          timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if config.get("MAIL_USERNAME"):
                smtp.login(config["MAIL_USERNAME"], config.get("MAIL_PASSWORD") or "")
            smtp.send_message(message)
    except Exception:
        # The recipient's address is personal data: log the subject only.
        app.logger.exception("Could not send an email (%s)", message["Subject"])
