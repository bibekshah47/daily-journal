
# Import Flask tools for creating pages and handling forms
from flask import Flask, render_template, request, redirect, url_for

# Import date and time tools
from datetime import date, datetime, timedelta

# Import SQLite to store journal entries
import sqlite3
import calendar


# Create the Flask application
app = Flask(__name__)


# Homepage: display, save, search, and filter journal entries
@app.route("/", methods=["GET", "POST"])
def home():

    # Get today's date
    today = date.today()

    # Message shown after submitting a journal entry
    message = None


    today = date.today()
    message = None

        
    # Get the selected month from the website URL
    # Example: /?month=2026-10
    selected_month = request.args.get("month", "")

    # Use the current month if no month was selected
    if selected_month:
        try:
            # Convert "2026-10" into a Python date
            displayed_month = datetime.strptime(
                selected_month, "%Y-%m"
            ).date()

        # Handle an invalid month in the URL
        except ValueError:
            displayed_month = today.replace(day=1)
    else:
        displayed_month = today.replace(day=1)

    # Get the year and month being displayed
    current_year = displayed_month.year
    current_month = displayed_month.month

    # Create a calendar starting on Sunday
    cal = calendar.Calendar(firstweekday=6)

    # Generate the calendar for the selected month
    calendar_weeks = cal.monthdatescalendar(
        current_year,
        current_month
    )

    # Calculate the previous month
    previous_month = (
        displayed_month.replace(day=1) - timedelta(days=1)
    ).strftime("%Y-%m")

    # Calculate the next month
    if current_month == 12:
        next_month = f"{current_year + 1}-01"
    else:
        next_month = f"{current_year}-{current_month + 1:02d}"

    # Print values to test
    print("Displayed month:", displayed_month)
    print("Previous month:", previous_month)
    print("Next month:", next_month)


    # Print the calendar weeks to test the result
    print("Calendar weeks:", calendar_weeks)


    # Get search and date filter values from the URL
    search_query = request.args.get("search", "")
    filter_date = request.args.get("filter_date", "")

    # Check whether the user submitted a new journal entry
    if request.method == "POST":

        # Get the journal text and selected mood
        journal_entry = request.form.get("journal")
        mood = request.form.get("mood")
        title = request.form.get("title", "").strip()


        # Prevent saving empty journal entries
        if journal_entry and journal_entry.strip():

            # Record the current date and time
            created_at = datetime.now().isoformat()

            # Connect to the database
            connection = sqlite3.connect("journal.db")
            cursor = connection.cursor()

            # Save journal text, date, and mood
            cursor.execute(
                """
                INSERT INTO entries (content, created_at, mood, title)
                VALUES (?, ?, ?, ?)
                """,
                (journal_entry, created_at, mood, title)
            )

            # Save the changes and close the connection
            connection.commit()
            connection.close()

            message = "Your journal entry was saved successfully!"

        else:
            message = "Please write something before saving."

    # Connect to the database to retrieve entries
    connection = sqlite3.connect("journal.db")
    cursor = connection.cursor()

    # Search by title or content, combined with date
    if search_query and filter_date:
        cursor.execute(
            """
            SELECT * FROM entries
            WHERE (title LIKE ? OR content LIKE ?)
            AND date(created_at) = ?
            ORDER BY id DESC
            """,
            (
                "%" + search_query + "%",
                "%" + search_query + "%",
                filter_date
            )
        )

    # Filter entries by date only
    elif filter_date:
        cursor.execute(
            """
            SELECT * FROM entries
            WHERE date(created_at) = ?
            ORDER BY id DESC
            """,
            (filter_date,)
        )

    
    # Search journal entries by title or content
    elif search_query:
        cursor.execute(
            """
            SELECT * FROM entries
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY id DESC
            """,
            (
                "%" + search_query + "%",
                "%" + search_query + "%"
            )
        )


    # Display all entries if no filters are selected
    else:
        cursor.execute(
            "SELECT * FROM entries ORDER BY id DESC"
        )

    # Get all matching journal entries
    rows = cursor.fetchall()


    # Count the total number of journal entries in the database
    cursor.execute("SELECT COUNT(*) FROM entries")

    # fetchone() returns one row, and [0] gets the count
    total_entries = cursor.fetchone()[0]


    # Get the current month in YYYY-MM format
    current_month = datetime.now().strftime("%Y-%m")

    # Count entries written during the current month
    cursor.execute(
        """
        SELECT COUNT(*) FROM entries
        WHERE substr(created_at, 1, 7) = ?
        """,
        (current_month,)
    )

    # Get the number returned by SQLite
    monthly_entries = cursor.fetchone()[0]

    # Find the mood that appears most frequently
    cursor.execute(
        """
        SELECT mood, COUNT(*) AS mood_count
        FROM entries
        WHERE mood IS NOT NULL AND mood != ''
        GROUP BY mood
        ORDER BY mood_count DESC, mood ASC
        LIMIT 1
        """
    )

    # Retrieve the most common mood
    mood_result = cursor.fetchone()

    # Show a default message if no moods have been recorded
    most_common_mood = mood_result[0] if mood_result else "No mood recorded"


    # Get all unique dates when journal entries were written
    cursor.execute(
        """
        SELECT DISTINCT date(created_at)
        FROM entries
        ORDER BY date(created_at) DESC
        """
    )

    # Retrieve the dates from SQLite
    journal_dates = cursor.fetchall()


    # Convert journal dates into a set of date strings
    # Example: {"2026-11-01", "2026-11-06"}

    calendar_entry_dates = {
        row[0]
        for row in journal_dates
        if row[0] is not None
    }

    # Print the dates to verify the result
    print("Calendar entry dates:", calendar_entry_dates)



    # Convert SQLite results into Python date objects
    dates = [
        date.fromisoformat(row[0])
        for row in journal_dates
        if row[0] is not None
    ]

    # Start the streak at zero
    current_streak = 0

    # Get today's date
    today_date = date.today()

    # A streak can continue from yesterday if
    # today's journal entry has not been written yet
    if today_date in dates:
        check_date = today_date
    else:
        check_date = today_date - timedelta(days=1)

    # Count consecutive journaling days
    for journal_date in dates:

        # Check whether the entry matches the expected date
        if journal_date == check_date:

            # Increase the streak by one day
            current_streak += 1

            # Move backward by one day
            check_date -= timedelta(days=1)

        # Stop when a day is missing
        else:
            break

    # Display the calculated streak in the terminal
    print("Current journaling streak:", current_streak)


    # Print the dates to understand the result
    print("Journal dates:", journal_dates)




    # Prepare entries for displaying on the website
    entries = []

    for entry in rows:

        # Get the journal entry information
        entry_id = entry[0]
        content = entry[1]
        created_at = datetime.fromisoformat(entry[2])
        mood = entry[3]
        title = entry[4]



        # Format the date into a readable format
        formatted_date = created_at.strftime(
            "%B %d, %Y • %I:%M %p"
        )

        # Store the entry information together
        entries.append(
            (entry_id, content, formatted_date, mood, title)
        )

    # Close the database connection
    connection.close()

    # Display the homepage with journal entries
    return render_template(
        "index.html",
        today=today,
        entries=entries,
        message=message,
        search_query=search_query,
        filter_date=filter_date,
        total_entries=total_entries, # Send the total number of entries to HTML
        monthly_entries=monthly_entries,             # Send the monthly entry count to HTML
        most_common_mood=most_common_mood, # Send the most common mood to HTML
        current_streak=current_streak,

    # Send calendar information to index.html
        calendar_weeks=calendar_weeks,
        calendar_entry_dates=calendar_entry_dates,
        current_month=current_month,

            # Calendar navigation information
        displayed_month=displayed_month,
        previous_month=previous_month,
        next_month=next_month,


    )


# Delete an existing journal entry
@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):

    # Connect to the database
    connection = sqlite3.connect("journal.db")
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
    connection = sqlite3.connect("journal.db")
    cursor = connection.cursor()

    # Check whether the user submitted the edit form
    if request.method == "POST":

        # Get the updated journal text and mood
        updated_content = request.form.get("journal")
        updated_mood = request.form.get("mood")
        updated_title = request.form.get("title", "").strip()


        # Make sure the updated journal text is not empty
        if updated_content and updated_content.strip():

            # Update both content and mood in the database
            cursor.execute(
                """
                UPDATE entries
                SET content = ?, mood = ?, title = ?
                WHERE id = ?
                """,
                (updated_content, updated_mood, updated_title, entry_id) 
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

    # Display the edit page
    return render_template("edit.html", entry=entry)



@app.route("/history")
def history():
    today = date.today()

    # Read filters from the URL
    search_query = request.args.get("search", "").strip()
    filter_date = request.args.get("filter_date", "").strip()
    selected_month = request.args.get("month", "").strip()

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
    connection = sqlite3.connect("journal.db")
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
            "time": created_at.strftime("%I:%M %p")
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
        calendar_entry_dates=calendar_entry_dates
    )



# View a single journal entry
@app.route("/entry/<int:entry_id>")
def view_entry(entry_id):

    # Connect to the database
    connection = sqlite3.connect("journal.db")
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

        # Prevent empty journal entries
        if not content:
            return render_template(
                "write.html",
                error="Please write something before saving.",
                title=title,
                mood=mood,
                content=content
            ), 400

        # Record the creation date and time
        created_at = datetime.now().isoformat()

        # Connect to SQLite
        connection = sqlite3.connect("journal.db")
        cursor = connection.cursor()

        # Save the journal entry
        cursor.execute("""
            INSERT INTO entries
                (title, content, mood, created_at)
            VALUES (?, ?, ?, ?)
        """, (title, content, mood, created_at))

        # Get the ID of the newly saved entry
        entry_id = cursor.lastrowid

        connection.commit()
        connection.close()

        # Open the newly created journal entry
        return redirect(
            url_for("view_entry", entry_id=entry_id)
        )

    # Display an empty writing form
    return render_template(
        "write.html",
        error=None,
        title="",
        mood="",
        content=""
    )


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=True, port=5001)
