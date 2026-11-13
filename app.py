
# Import Flask tools for creating pages and handling forms
from flask import Flask, render_template, request, redirect, url_for

# Import date and time tools
from datetime import date, datetime, timedelta

# Import SQLite to store journal entries
import sqlite3
import calendar
import os
from database import initialize_database


# Create the Flask application
app = Flask(__name__)


# Allow isolated databases for testing; normal startup upgrades journal.db.
app.config["DATABASE"] = os.environ.get("JOURNAL_DATABASE", "journal.db")
initialize_database(app.config["DATABASE"])

REFLECTION_FIELDS = ("went_well", "went_wrong", "improve_tomorrow")


# Home Dashboard
@app.route("/")
def home():

    # Get today's date and current time
    today = date.today()
    current_hour = datetime.now().hour

    # Choose a greeting based on the time
    if current_hour < 12:
        greeting = "Good morning"
    elif current_hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    # Connect to SQLite
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    # 1. Count all journal entries
    cursor.execute("SELECT COUNT(*) FROM entries")
    total_entries = cursor.fetchone()[0]

    # 2. Count entries from the current month
    current_month = today.strftime("%Y-%m")

    cursor.execute("""
        SELECT COUNT(*)
        FROM entries
        WHERE substr(created_at, 1, 7) = ?
    """, (current_month,))

    monthly_entries = cursor.fetchone()[0]

    # 3. Get the unique dates when journals were written
    cursor.execute("""
        SELECT DISTINCT date(created_at) AS journal_date
        FROM entries
        WHERE date(created_at) IS NOT NULL
        ORDER BY journal_date DESC
    """)

    journal_dates = {
        date.fromisoformat(row["journal_date"])
        for row in cursor.fetchall()
    }

    # Calculate the current writing streak
    current_streak = 0

    # A streak is still active if the user wrote yesterday
    if today in journal_dates:
        check_date = today
    else:
        check_date = today - timedelta(days=1)

    while check_date in journal_dates:
        current_streak += 1
        check_date -= timedelta(days=1)

    # 4. Get the three most recent journal entries
    cursor.execute("""
        SELECT id, title, content, mood, created_at
        FROM entries
        ORDER BY created_at DESC, id DESC
        LIMIT 3
    """)

    rows = cursor.fetchall()

    recent_entries = []

    for row in rows:
        created_at = datetime.fromisoformat(
            row["created_at"]
        )

        # Use the date as the title if no title was entered
        entry_title = row["title"]

        if not entry_title:
            entry_title = created_at.strftime("%B %d, %Y")

        recent_entries.append({
            "id": row["id"],
            "title": entry_title,
            "content": row["content"],
            "mood": row["mood"],
            "date": created_at.strftime("%B %d, %Y"),
            "time": created_at.strftime("%I:%M %p")
        })

    connection.close()

    # Display the Home Dashboard
    return render_template(
        "index.html",
        greeting=greeting,
        today=today,
        total_entries=total_entries,
        monthly_entries=monthly_entries,
        current_streak=current_streak,
        recent_entries=recent_entries
    )


# Delete an existing journal entry
@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):

    # Connect to the database
    connection = sqlite3.connect(app.config["DATABASE"])
    cursor = connection.cursor()

    # Delete the entry with the matching ID
    cursor.execute(
        "DELETE FROM entries WHERE id = ?",
        (entry_id,)
    )

    # Save the deletion and close the connection
    connection.commit()
    connection.close()

    # Return to the homepage
    return redirect(url_for("history"))


# Edit an existing journal entry
@app.route("/edit/<int:entry_id>", methods=["GET", "POST"])
def edit_entry(entry_id):

    # Connect to the database
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    # Check whether the user submitted the edit form
    if request.method == "POST":

        # Get the updated journal text and mood
        updated_content = request.form.get("journal")
        updated_mood = request.form.get("mood")
        updated_title = request.form.get("title", "").strip()
        reflections = {field: request.form.get(field, "").strip()
                       for field in REFLECTION_FIELDS}


        # Make sure the updated journal text is not empty
        if updated_content and updated_content.strip():

            # Update both content and mood in the database
            cursor.execute(
                """
                UPDATE entries
                SET content = ?, mood = ?, title = ?,
                    went_well = ?, went_wrong = ?, improve_tomorrow = ?
                WHERE id = ?
                """,
                (updated_content, updated_mood, updated_title,
                 *(reflections[field] for field in REFLECTION_FIELDS), entry_id)
            )

            # Save the updated entry
            connection.commit()
            connection.close()

            # Return to the homepage
            return redirect(url_for("view_entry", entry_id=entry_id))

    # Retrieve the journal entry being edited
    cursor.execute(
        "SELECT * FROM entries WHERE id = ?",
        (entry_id,)
    )

    entry = cursor.fetchone()
    connection.close()

    # Show an error if the entry does not exist
    if entry is None:
        return "Journal entry not found", 404

    # Keep every submitted field when validation fails.
    if request.method == "POST":
        entry = dict(entry)
        entry.update(title=updated_title, content=updated_content or "",
                     mood=updated_mood, **reflections)
        return render_template("edit.html", entry=entry,
                               error="Please write something before saving."), 400

    return render_template("edit.html", entry=entry)


# Favorite or unfavorite a journal entry
@app.route("/favorite/<int:entry_id>", methods=["POST"])
def toggle_favorite(entry_id):

    connection = sqlite3.connect(app.config["DATABASE"])
    cursor = connection.cursor()

    # Toggle the favorite value between 0 and 1
    cursor.execute("""
        UPDATE entries
        SET is_favorite = CASE
            WHEN is_favorite = 1 THEN 0
            ELSE 1
        END
        WHERE id = ?
    """, (entry_id,))

    connection.commit()
    connection.close()

    # Return to Journal History
    # Preserve the active search, date and favorites filters
    params = {
        "search": request.form.get("search", ""),
        "filter_date": request.form.get("filter_date", ""),
        "month": request.form.get("month", ""),
        "favorites": request.form.get("favorites", "")
    }

    params = {
        key: value
        for key, value in params.items()
        if value
    }

    return redirect(
        url_for("history", **params) + "#journal-results"
    )


@app.route("/history")
def history():
    today = date.today()

    # Read filters from the URL
    search_query = request.args.get("search", "").strip()
    filter_date = request.args.get("filter_date", "").strip()
    selected_month = request.args.get("month", "").strip()
    favorites_only = request.args.get("favorites", "") == "1"

    # Validate the selected date
    if filter_date:
        try:
            date.fromisoformat(filter_date)
        except ValueError:
            filter_date = ""

    # Decide which month the calendar should display
    try:
        if selected_month:
            displayed_month = datetime.strptime(
                selected_month, "%Y-%m"
            ).date()
        elif filter_date:
            displayed_month = date.fromisoformat(
                filter_date
            ).replace(day=1)
        else:
            displayed_month = today.replace(day=1)
    except ValueError:
        displayed_month = today.replace(day=1)

    current_year = displayed_month.year
    current_month = displayed_month.month

    # Generate the calendar
    cal = calendar.Calendar(firstweekday=6)
    calendar_weeks = cal.monthdatescalendar(
        current_year, current_month
    )

    previous_month = (
        displayed_month - timedelta(days=1)
    ).strftime("%Y-%m")

    if current_month == 12:
        next_month = f"{current_year + 1}-01"
    else:
        next_month = f"{current_year}-{current_month + 1:02d}"

    # Connect to SQLite
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    # Get dates that contain journal entries
    cursor.execute("""
        SELECT DISTINCT date(created_at) AS entry_date
        FROM entries
    """)

    calendar_entry_dates = {
        row["entry_date"]
        for row in cursor.fetchall()
    }

    # Build the journal search query
    query = "SELECT * FROM entries WHERE 1=1"
    parameters = []

        # Show only favorite journals when requested
    if favorites_only:
        query += " AND is_favorite = 1"

    if search_query:
        query += " AND (title LIKE ? OR content LIKE ?)"
        keyword = f"%{search_query}%"
        parameters.extend([keyword, keyword])

    if filter_date:
        query += " AND date(created_at) = ?"
        parameters.append(filter_date)

    query += " ORDER BY created_at DESC, id DESC"

    cursor.execute(query, parameters)
    rows = cursor.fetchall()

    # Count all saved entries
    cursor.execute("SELECT COUNT(*) FROM entries")
    total_entries = cursor.fetchone()[0]

    connection.close()

    # Format entries for display
    entries = []

    for row in rows:
        created_at = datetime.fromisoformat(
            row["created_at"]
        )

        entries.append({
            "id": row["id"],
            "title": row["title"],
            "content": row["content"],
            "mood": row["mood"],
            "date": created_at.strftime("%B %d, %Y"),
            "time": created_at.strftime("%I:%M %p"),
            "is_favorite": row["is_favorite"]
        })

    return render_template(
        "history.html",
        entries=entries,
        total_entries=total_entries,
        search_query=search_query,
        filter_date=filter_date,
        displayed_month=displayed_month,
        current_month=current_month,
        calendar_weeks=calendar_weeks,
        previous_month=previous_month,
        next_month=next_month,
        calendar_entry_dates=calendar_entry_dates,
        favorites_only=favorites_only,

    )



# View a single journal entry
@app.route("/entry/<int:entry_id>")
def view_entry(entry_id):

    # Connect to the database
    connection = sqlite3.connect(app.config["DATABASE"])
    cursor = connection.cursor()

    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    # Find the journal entry using its ID
    cursor.execute(
        "SELECT * FROM entries WHERE id = ?",
        (entry_id,)
    )

    entry = cursor.fetchone()
    connection.close()

    # Show an error if the entry doesn't exist
    if entry is None:
        return "Journal entry not found", 404

    # Send the entry to the HTML page
    return render_template("entry.html", entry=entry)


# Dedicated Write Journal page
@app.route("/write", methods=["GET", "POST"])
def write_journal():

    # When the user submits the journal form
    if request.method == "POST":

        title = request.form.get("title", "").strip()
        mood = request.form.get("mood", "").strip()
        content = request.form.get("journal", "").strip()
        reflections = {field: request.form.get(field, "").strip()
                       for field in REFLECTION_FIELDS}

        # Prevent empty journal entries
        if not content:
            return render_template(
                "write.html",
                error="Please write something before saving.",
                title=title,
                mood=mood,
                content=content,
                **reflections
            ), 400

        # Record the creation date and time
        created_at = datetime.now().isoformat()

        # Connect to SQLite
        connection = sqlite3.connect(app.config["DATABASE"])
        cursor = connection.cursor()

        # Save the journal entry
        cursor.execute("""
            INSERT INTO entries
                (title, content, mood, created_at,
                 went_well, went_wrong, improve_tomorrow)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (title, content, mood, created_at,
              *(reflections[field] for field in REFLECTION_FIELDS)))

        # Get the ID of the newly saved entry
        entry_id = cursor.lastrowid

        connection.commit()
        connection.close()

        # Open the newly created journal entry
        return redirect(
            url_for("view_entry", entry_id=entry_id, saved="1")
        )

    # Display an empty writing form
    return render_template(
        "write.html",
        error=None,
        title="",
        mood="",
        content="",
        **{field: "" for field in REFLECTION_FIELDS}
    )


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=True, port=5001)
