"""History: the calendar, search, favorites and tag filters."""
import calendar
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request

from db import get_db
from tags import normalize_tag
from views.shared import attach_tags, entry_summary, parse_timestamp, trash_count

bp = Blueprint("history", __name__)


@bp.route("/history")
def history():
    today = date.today()

    # Read filters from the URL
    search_query = request.args.get("search", "").strip()
    filter_date = request.args.get("filter_date", "").strip()
    selected_month = request.args.get("month", "").strip()
    favorites_only = request.args.get("favorites", "") == "1"
    active_tag = normalize_tag(request.args.get("tag", ""))

    # Validate the selected date
    if filter_date:
        try:
            date.fromisoformat(filter_date)
        except ValueError:
            filter_date = ""

    # Decide which month the calendar should display
    try:
        if selected_month:
            displayed_month = datetime.strptime(selected_month, "%Y-%m").date()
        elif filter_date:
            displayed_month = date.fromisoformat(filter_date).replace(day=1)
        else:
            displayed_month = today.replace(day=1)
    except ValueError:
        displayed_month = today.replace(day=1)

    current_year = displayed_month.year
    current_month = displayed_month.month

    # Generate the calendar
    cal = calendar.Calendar(firstweekday=6)
    calendar_weeks = cal.monthdatescalendar(current_year, current_month)

    previous_month = (displayed_month - timedelta(days=1)).strftime("%Y-%m")
    if current_month == 12:
        next_month = f"{current_year + 1}-01"
    else:
        next_month = f"{current_year}-{current_month + 1:02d}"

    connection = get_db()

    # Dates that contain journal entries, with the mood of the latest one
    calendar_moods = {}
    for row in connection.execute("""
        SELECT date(created_at) AS entry_date, mood
        FROM live_entries
        ORDER BY created_at
    """).fetchall():
        calendar_moods[row["entry_date"]] = row["mood"] or ""
    calendar_entry_dates = set(calendar_moods)

    # Build the journal search query from fixed SQL pieces; every value is a ? parameter.
    query = "SELECT * FROM live_entries WHERE 1=1"
    parameters = []

    if favorites_only:
        query += " AND is_favorite = 1"

    if search_query:
        query += """ AND (title LIKE ? OR content LIKE ? OR id IN (
            SELECT entry_tags.entry_id FROM entry_tags
            JOIN tags ON tags.id = entry_tags.tag_id
            WHERE tags.name LIKE ?))"""
        keyword = f"%{search_query}%"
        # "#work" should find the tag "work".
        tag_keyword = f"%{search_query.lstrip('#')}%"
        parameters.extend([keyword, keyword, tag_keyword])

    if active_tag:
        query += """ AND id IN (
            SELECT entry_tags.entry_id FROM entry_tags
            JOIN tags ON tags.id = entry_tags.tag_id
            WHERE tags.name = ?)"""
        parameters.append(active_tag)

    if filter_date:
        query += " AND date(created_at) = ?"
        parameters.append(filter_date)

    query += " ORDER BY created_at DESC, id DESC"

    rows = connection.execute(query, parameters).fetchall()

    total_entries = connection.execute("SELECT COUNT(*) FROM live_entries").fetchone()[0]
    trashed_entries = trash_count(connection)
    entries = attach_tags(connection, [entry_summary(row) for row in rows])

    # Group entries by month so the list is easier to scan
    groups = []
    for row, entry in zip(rows, entries):
        created_at = parse_timestamp(row["created_at"])
        label = created_at.strftime("%B %Y") if created_at else "Other"
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "entries": []})
        groups[-1]["entries"].append(entry)

    return render_template(
        "history.html",
        entries=entries,
        entry_groups=groups,
        total_entries=total_entries,
        search_query=search_query,
        filter_date=filter_date,
        displayed_month=displayed_month,
        current_month=current_month,
        calendar_weeks=calendar_weeks,
        previous_month=previous_month,
        next_month=next_month,
        calendar_entry_dates=calendar_entry_dates,
        calendar_moods=calendar_moods,
        favorites_only=favorites_only,
        active_tag=active_tag,
        trashed_entries=trashed_entries,
        today=today,
    )
