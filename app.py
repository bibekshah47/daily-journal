
from flask import Flask, render_template, request
from datetime import date

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def home():
    today = date.today()
    message = None

    if request.method == "POST":
        journal_entry = request.form.get("journal")

        if journal_entry and journal_entry.strip():
            print("Journal Entry:", journal_entry)
            message = "Your journal entry was submitted successfully!"
        else:
            message = "Please write something before saving."

    return render_template("index.html", today=today, message=message)

if __name__ == "__main__":
    app.run(debug=True, port=5001)
