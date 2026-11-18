"""The two clocks: the user's local time, and system time (UTC).

The server may run on UTC, but "today" for a journal means the writer's own
day. Anything a person sees or writes (the greeting, today's entry, streaks,
the week, new entries' created_at, Trash dates, export names) uses the
signed-in user's time zone. System bookkeeping (login lockouts, account
created_at, migrations, the daily SQLite backup, the Trash cleanup) uses UTC.

Every timestamp is stored as naive ISO text ("2026-11-17T21:04:05.123456"),
the same format as existing data: user times are the user's wall clock, and
system times are UTC.

`clock()` is the only place the real time is read; tests patch it.
"""
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from zoneinfo import ZoneInfo, available_timezones

DEFAULT_TIMEZONE = "UTC"
UTC = ZoneInfo("UTC")

# In tzdata but not a place anyone lives.
_NOT_A_PLACE = {"Factory", "localtime", "posixrules"}


def clock():
    """The current moment, time-zone aware (UTC)."""
    return datetime.now(timezone.utc)


# ---------- Time zone names ----------

@lru_cache(maxsize=1)
def valid_timezones():
    """Every time zone name accepted: zoneinfo's list (tzdata), minus
    placeholders. Includes old aliases such as Asia/Calcutta."""
    return frozenset(available_timezones() - _NOT_A_PLACE)


@lru_cache(maxsize=1)
def _aliases():
    """{old name: current name} from tzdata's link list ("L Asia/Kolkata
    Asia/Calcutta"). Browsers still report some old names (Chrome says
    Asia/Calcutta and Asia/Katmandu). Empty if tzdata isn't installed."""
    try:
        text = resources.files("tzdata").joinpath("zoneinfo", "tzdata.zi").read_text()
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return {}
    links = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "L":
            links[parts[2]] = parts[1]
    return links


def canonical_timezone(name):
    """The current name for a valid zone (Asia/Calcutta -> Asia/Kolkata).
    UTC and its aliases (Etc/UTC, Etc/Universal...) are all "UTC"."""
    current = _aliases().get(name, name)
    return DEFAULT_TIMEZONE if current in ("Etc/UTC", DEFAULT_TIMEZONE) else current


@lru_cache(maxsize=1)
def timezone_choices():
    """The Settings menu: each zone once, by its current name, sorted."""
    return tuple(sorted({canonical_timezone(name) for name in valid_timezones()}))


@lru_cache(maxsize=1)
def timezone_aliases():
    """{current name: "old names" (space-separated)}, for matching a browser
    that reports an old name to the right menu entry."""
    grouped = {}
    for name in sorted(valid_timezones()):
        current = canonical_timezone(name)
        if current != name:
            grouped.setdefault(current, []).append(name)
    return {current: " ".join(names) for current, names in grouped.items()}


def is_valid_timezone(name):
    return isinstance(name, str) and name in valid_timezones()


def normalize_timezone(name):
    """The current name of a valid time zone (trimmed), else "UTC"."""
    name = str(name or "").strip()
    return canonical_timezone(name) if is_valid_timezone(name) else DEFAULT_TIMEZONE


def zone(name):
    """The ZoneInfo for a stored name; UTC for a missing or invalid one."""
    return ZoneInfo(normalize_timezone(name))


# ---------- The user's clock ----------

def user_tz():
    """The signed-in user's time zone (UTC when logged out or unknown)."""
    from flask import has_request_context
    if not has_request_context():
        return UTC
    from flask_login import current_user
    if not current_user.is_authenticated:
        return UTC
    return zone(getattr(current_user, "timezone", None))


def now_in(tz):
    """Wall-clock time now in `tz` (a ZoneInfo or a name), as a naive datetime."""
    if not isinstance(tz, ZoneInfo):
        tz = zone(tz)
    return clock().astimezone(tz).replace(tzinfo=None)


def user_now():
    """The signed-in user's wall-clock time, naive (for storing and showing)."""
    return now_in(user_tz())


def user_today():
    return user_now().date()


# ---------- System time ----------

def utc_now():
    """UTC now, naive, for system timestamps (lockouts, created_at, backups)."""
    return clock().astimezone(timezone.utc).replace(tzinfo=None)


def utc_today():
    return utc_now().date()
