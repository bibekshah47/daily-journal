
from flask import Flask, render_template, request, redirect, url_for
from datetime import date, datetime
import sqlite3

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def home():
    today = date.today()
    message = None


    search_query = request.args.get("search", "")

    filter_date = request.args.get("filter_date", "")
    print("Selected date:", filter_date)

    print("Search keyword:", search_query)


    if request.method == "POST":
        journal_entry = request.form.get("journal")

        if journal_entry and journal_entry.strip():
            connection = sqlite3.connect("journal.db")
            cursor = connection.cursor()

            cursor.execute(
                "INSERT INTO entries (content, created_at) VALUES (?, ?)",
                (journal_entry, datetime.now().isoformat())
            )

            connection.commit()
            connection.close()

            message = "Your journal entry was saved successfully!"
        else:
            message = "Please write something before saving."

    
    connection = sqlite3.connect("journal.db")
    cursor = connection.cursor()

    
    
    

    # Check whether both a keyword and a date were provided
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

    # Filter entries using only the selected date
    elif filter_date:
        cursor.execute(
            "SELECT * FROM entries WHERE date(created_at) = ? ORDER BY id DESC",
            (filter_date,)
        )

    # Filter entries using only the search keyword
    elif search_query:
        cursor.execute(
            "SELECT * FROM entries WHERE content LIKE ? ORDER BY id DESC",
            ("%" + search_query + "%",)
        )

    # If no filters are provided, display all entries
    else:
        cursor.execute("SELECT * FROM entries ORDER BY id DESC")

    # Retrieve the matching entries from SQLite
    rows = cursor.fetchall()




    entries = []

    for entry in rows:
        entry_id = entry[0]
        content = entry[1]
        created_at = datetime.fromisoformat(entry[2])

        formatted_date = created_at.strftime("%B %d, %Y • %I:%M %p")

        entries.append((entry_id, content, formatted_date))


    connection.close()

    return render_template(
        "index.html",
        today=today,
        entries=entries,
        message=message,

        # Pass the search keyword to the HTML template
        search_query=search_query,

        # Remember the date selected in the date picker
        filter_date=filter_date
    )


@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):
    connection = sqlite3.connect("journal.db")
    cursor = connection.cursor()

    cursor.execute(
        "DELETE FROM entries WHERE id = ?",
        (entry_id,)
    )

    connection.commit()
    connection.close()

    return redirect(url_for("home"))


@app.route("/edit/<int:entry_id>", methods=["GET", "POST"])
def edit_entry(entry_id):
    connection = sqlite3.connect("journal.db")
    cursor = connection.cursor()

    if request.method == "POST":
        updated_content = request.form.get("journal")

        if updated_content and updated_content.strip():
            cursor.execute(
                "UPDATE entries SET content = ? WHERE id = ?",
                (updated_content, entry_id)
            )

            connection.commit()
            connection.close()

            return redirect(url_for("home"))

    cursor.execute(
        "SELECT * FROM entries WHERE id = ?",
        (entry_id,)
    )

    entry = cursor.fetchone()
    connection.close()

    if entry is None:
        return "Journal entry not found", 404

    return render_template("edit.html", entry=entry)



if __name__ == "__main__":
    app.run(debug=True, port=5001)
