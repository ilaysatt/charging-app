import csv
import io
import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response

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
            vin TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Add vin column to existing databases
    try:
        conn.execute("ALTER TABLE charges ADD COLUMN vin TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
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

    filter_vin = request.args.get("vin", "")
    filter_month = request.args.get("month", "")

    query = "SELECT * FROM charges WHERE 1=1"
    params = []
    if filter_vin:
        query += " AND vin = ?"
        params.append(filter_vin)
    if filter_month:
        query += " AND strftime('%Y-%m', date) = ?"
        params.append(filter_month)
    query += " ORDER BY date DESC, id DESC"

    charges = conn.execute(query, params).fetchall()
    total_kwh = sum(c["kwh"] for c in charges)
    count = len(charges)
    avg_kwh = total_kwh / count if count > 0 else 0

    # Get distinct VINs and months for filter dropdowns
    vins = [r[0] for r in conn.execute(
        "SELECT DISTINCT vin FROM charges WHERE vin != '' ORDER BY vin"
    ).fetchall()]
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', date) FROM charges ORDER BY 1 DESC"
    ).fetchall()]

    kwh_per_pct = get_kwh_per_pct()
    conn.close()
    return render_template(
        "index.html",
        charges=charges,
        total_kwh=round(total_kwh, 2),
        avg_kwh=round(avg_kwh, 2),
        count=count,
        kwh_per_pct=kwh_per_pct,
        vins=vins,
        months=months,
        filter_vin=filter_vin,
        filter_month=filter_month,
    )


@app.route("/add", methods=["POST"])
def add_charge():
    input_method = request.form.get("input_method")
    date_str = request.form.get("date") or datetime.now().strftime("%Y-%m-%d")

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

    vin = (request.form.get("vin") or "").strip().upper()

    conn = get_db()
    conn.execute(
        """INSERT INTO charges (date, kwh, start_pct, end_pct, input_method, vin)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (date_str, kwh, start_pct, end_pct, input_method, vin),
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


@app.route("/export")
def export_csv():
    conn = get_db()
    charges = conn.execute(
        "SELECT * FROM charges ORDER BY date DESC, id DESC"
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "VIN", "kWh", "Start %", "End %", "Input Method"])
    for c in charges:
        writer.writerow([
            c["date"],
            c["vin"],
            c["kwh"],
            c["start_pct"] if c["start_pct"] is not None else "",
            c["end_pct"] if c["end_pct"] is not None else "",
            c["input_method"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=charges.csv"},
    )


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
