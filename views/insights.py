"""Insights and the weekly review."""
from datetime import date, timedelta

from flask import Blueprint, render_template, request

from db import get_db
from journal_insights import REFLECTION_QUESTIONS, get_journal_stats
from views.shared import MOODS
from weekly import get_week_review, week_bounds, week_label

bp = Blueprint("insights", __name__)


@bp.route("/insights")
def insights():
    connection = get_db()
    stats = get_journal_stats(connection)
    mood_counts = dict(connection.execute(
        "SELECT mood, COUNT(*) FROM live_entries WHERE mood IS NOT NULL AND mood != '' GROUP BY mood"
    ).fetchall())

    mood_breakdown = [
        {**mood, "count": mood_counts.get(mood["name"], 0)} for mood in MOODS
    ]
    most_entries = max([mood["count"] for mood in mood_breakdown] + [1])
    return render_template("insights.html", mood_breakdown=mood_breakdown,
                           most_mood_entries=most_entries, **stats)


@bp.route("/week")
def week():
    """Weekly review. ?start=YYYY-MM-DD snaps to its Sunday; anything else shows this week."""
    this_week, _ = week_bounds(date.today())
    try:
        start, _ = week_bounds(date.fromisoformat(request.args.get("start", "").strip()))
        if start > this_week:
            raise ValueError("future week")
        previous_start = start - timedelta(days=7)
    except (ValueError, OverflowError):  # OverflowError: weeks before year 1
        start = this_week
        previous_start = start - timedelta(days=7)

    review = get_week_review(get_db(), start)

    # No arrow into the future (and no date overflow near year 9999).
    next_start = start + timedelta(days=7) if start < this_week else None
    return render_template(
        "week.html",
        review=review,
        week_label=week_label(start),
        today=date.today(),
        questions=REFLECTION_QUESTIONS,
        previous_start=previous_start.isoformat(),
        next_start=next_start.isoformat() if next_start else None,
        is_this_week=start == this_week,
    )
