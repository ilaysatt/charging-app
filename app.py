import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "charging.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            kwh REAL NOT NULL,
            start_pct REAL,
            end_pct REAL,
            input_method TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Default kWh per percent - user can change in settings
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("kwh_per_pct", "2.05"),
    )
    conn.commit()
    conn.close()


def get_kwh_per_pct():
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'kwh_per_pct'"
    ).fetchone()
    conn.close()
    return float(row["value"]) if row else 2.05


@app.route("/")
def index():
    conn = get_db()
    charges = conn.execute(
        "SELECT * FROM charges ORDER BY date DESC, id DESC"
    ).fetchall()
    total_kwh = conn.execute("SELECT COALESCE(SUM(kwh), 0) as total FROM charges").fetchone()["total"]
    count = len(charges)
    avg_kwh = total_kwh / count if count > 0 else 0
    kwh_per_pct = get_kwh_per_pct()
    conn.close()
    return render_template(
        "index.html",
        charges=charges,
        total_kwh=round(total_kwh, 2),
        avg_kwh=round(avg_kwh, 2),
        count=count,
        kwh_per_pct=kwh_per_pct,
    )


@app.route("/add", methods=["POST"])
def add_charge():
    input_method = request.form.get("input_method")
    date_str = request.form.get("date") or datetime.now().strftime("%Y-%m-%dT%H:%M")

    if input_method == "kwh":
        kwh = float(request.form.get("kwh") or 0)
        start_pct = None
        end_pct = None
    elif input_method == "percentage":
        start_pct = float(request.form.get("start_pct") or 0)
        end_pct = float(request.form.get("end_pct") or 0)
        kwh_per_pct = float(request.form.get("kwh_per_pct") or get_kwh_per_pct())
        kwh = round((end_pct - start_pct) * kwh_per_pct, 2)
    else:
        return redirect(url_for("index"))

    if kwh <= 0:
        return redirect(url_for("index"))

    conn = get_db()
    conn.execute(
        """INSERT INTO charges (date, kwh, start_pct, end_pct, input_method)
           VALUES (?, ?, ?, ?, ?)""",
        (date_str, kwh, start_pct, end_pct, input_method),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete/<int:charge_id>", methods=["POST"])
def delete_charge(charge_id):
    conn = get_db()
    conn.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/settings", methods=["POST"])
def update_settings():
    kwh_per_pct = request.form.get("kwh_per_pct")
    if kwh_per_pct:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("kwh_per_pct", str(float(kwh_per_pct))),
        )
        conn.commit()
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5050)
