
# Import Flask tools for creating pages and handling forms
from flask import Flask, render_template, request, redirect, url_for

# Import date and time tools
from datetime import date, datetime, timedelta

# Import SQLite to store journal entries
import sqlite3

# Create the Flask application
app = Flask(__name__)


# Homepage: display, save, search, and filter journal entries
@app.route("/", methods=["GET", "POST"])
def home():

    # Get today's date
    today = date.today()

    # Message shown after submitting a journal entry
    message = None

    # Get search and date filter values from the URL
    search_query = request.args.get("search", "")
    filter_date = request.args.get("filter_date", "")

    # Check whether the user submitted a new journal entry
    if request.method == "POST":

        # Get the journal text and selected mood
        journal_entry = request.form.get("journal")
        mood = request.form.get("mood")

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
                INSERT INTO entries (content, created_at, mood)
                VALUES (?, ?, ?)
                """,
                (journal_entry, created_at, mood)
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

    # Search by keyword and date together
    if search_query and filter_date:
        cursor.execute(
            """
            SELECT * FROM entries
            WHERE content LIKE ?
            AND date(created_at) = ?
            ORDER BY id DESC
            """,
            ("%" + search_query + "%", filter_date)
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

    # Search entries by keyword only
    elif search_query:
        cursor.execute(
            """
            SELECT * FROM entries
            WHERE content LIKE ?
            ORDER BY id DESC
            """,
            ("%" + search_query + "%",)
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

        # Format the date into a readable format
        formatted_date = created_at.strftime(
            "%B %d, %Y • %I:%M %p"
        )

        # Store the entry information together
        entries.append(
            (entry_id, content, formatted_date, mood)
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
    return redirect(url_for("home"))


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

        # Make sure the updated journal text is not empty
        if updated_content and updated_content.strip():

            # Update both content and mood in the database
            cursor.execute(
                """
                UPDATE entries
                SET content = ?, mood = ?
                WHERE id = ?
                """,
                (updated_content, updated_mood, entry_id)
            )

            # Save the updated entry
            connection.commit()
            connection.close()

            # Return to the homepage
            return redirect(url_for("home"))

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


# Start the Flask development server
if __name__ == "__main__":
    app.run(debug=True, port=5001)
