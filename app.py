
from flask import Flask, render_template
from datetime import date

app = Flask(__name__)

@app.route("/")
def home():
    today = date.today()
    return render_template("index.html", today=today)

if __name__ == "__main__":
    app.run(debug=True, port=5001)
