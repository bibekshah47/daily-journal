
from flask import Flask, render_template, request
from datetime import date, datetime
import sqlite3

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def home():
    today = date.today()
    message = None

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

    cursor.execute("SELECT * FROM entries ORDER BY id DESC")
    entries = cursor.fetchall()

    connection.close()

    return render_template(
        "index.html",
        today=today,
        message=message,
        entries=entries
    )

if __name__ == "__main__":
    app.run(debug=True, port=5001)
