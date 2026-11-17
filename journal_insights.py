"""Read-only journal statistics shared by Home and Insights."""
from datetime import date, timedelta

REFLECTION_QUESTIONS = (
    ('went_well', 'What went well today?'),
    ('went_wrong', "What didn't go well today?"),
    ('improve_tomorrow', 'What can I improve tomorrow?'),
)


def get_journal_stats(connection, today=None, *, user_id):
    today = today or date.today()
    rows = connection.execute('''
        SELECT created_at, went_well, went_wrong, improve_tomorrow FROM live_entries
        WHERE user_id = ?
    ''', (user_id,)).fetchall()
    dates = set()
    monthly_entries = 0
    reflection_entries = 0
    answers = [0, 0, 0]
    for timestamp, *reflections in rows:
        # Entries use local ISO timestamps; ignore malformed dates in streaks.
        try:
            written = date.fromisoformat(timestamp[:10])
        except (TypeError, ValueError):
            written = None
        if written:
            dates.add(written)
            monthly_entries += (written.year, written.month) == (today.year, today.month)
        answered = [bool((answer or '').strip()) for answer in reflections]
        reflection_entries += any(answered)
        answers = [count + present for count, present in zip(answers, answered)]

    current_streak = 0
    check_date = today if today in dates else today - timedelta(days=1)
    while check_date in dates:
        current_streak += 1
        check_date -= timedelta(days=1)

    longest_streak = run = 0
    previous = None
    for written in sorted(dates):
        run = run + 1 if previous and written - previous == timedelta(days=1) else 1
        longest_streak = max(longest_streak, run)
        previous = written

    top_count = max(answers, default=0)
    return {
        'total_entries': len(rows),
        'monthly_entries': monthly_entries,
        'current_streak': current_streak,
        'longest_streak': longest_streak,
        'reflection_entries': reflection_entries,
        'reflection_rate': 100 * reflection_entries / len(rows) if rows else 0,
        'top_questions': [question for (_, question), count in zip(REFLECTION_QUESTIONS, answers)
                          if count == top_count and top_count > 0],
        'top_question_count': top_count,
    }
