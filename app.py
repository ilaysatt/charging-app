import csv
import io
import secrets
import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, Response, abort
from fpdf import FPDF

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "charging.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            session_token TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (session_token, key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_token TEXT NOT NULL,
            date TEXT NOT NULL,
            kwh REAL NOT NULL,
            start_pct REAL,
            end_pct REAL,
            input_method TEXT NOT NULL,
            vin TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def create_session(name=""):
    token = secrets.token_urlsafe(12)
    conn = get_db()
    conn.execute("INSERT INTO sessions (token, name) VALUES (?, ?)", (token, name))
    conn.execute(
        "INSERT INTO settings (session_token, key, value) VALUES (?, ?, ?)",
        (token, "kwh_per_pct", "2.05"),
    )
    conn.execute(
        "INSERT INTO settings (session_token, key, value) VALUES (?, ?, ?)",
        (token, "price_per_kwh", "0.35"),
    )
    conn.commit()
    conn.close()
    return token


def get_session_or_404(token):
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    return row


def get_setting(token, key, default):
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM settings WHERE session_token = ? AND key = ?", (token, key)
    ).fetchone()
    conn.close()
    return float(row["value"]) if row else default


# --- Landing page ---

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/new", methods=["POST"])
def new_session():
    name = (request.form.get("name") or "").strip()
    token = create_session(name)
    return redirect(url_for("index", token=token))


# --- Session-scoped routes ---

@app.route("/s/<token>")
def index(token):
    get_session_or_404(token)
    conn = get_db()

    filter_vin = request.args.get("vin", "")
    filter_month = request.args.get("month", "")

    query = "SELECT * FROM charges WHERE session_token = ?"
    params = [token]
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

    vins = [r[0] for r in conn.execute(
        "SELECT DISTINCT vin FROM charges WHERE session_token = ? AND vin != '' ORDER BY vin",
        (token,),
    ).fetchall()]
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', date) FROM charges WHERE session_token = ? ORDER BY 1 DESC",
        (token,),
    ).fetchall()]

    kwh_per_pct = get_setting(token, "kwh_per_pct", 2.05)
    price_per_kwh = get_setting(token, "price_per_kwh", 0.35)
    conn.close()
    return render_template(
        "index.html",
        token=token,
        charges=charges,
        total_kwh=round(total_kwh, 2),
        avg_kwh=round(avg_kwh, 2),
        count=count,
        kwh_per_pct=kwh_per_pct,
        price_per_kwh=price_per_kwh,
        vins=vins,
        months=months,
        filter_vin=filter_vin,
        filter_month=filter_month,
    )


@app.route("/s/<token>/add", methods=["POST"])
def add_charge(token):
    get_session_or_404(token)
    input_method = request.form.get("input_method")
    date_str = request.form.get("date") or datetime.now().strftime("%Y-%m-%d")

    if input_method == "kwh":
        kwh = float(request.form.get("kwh") or 0)
        start_pct = None
        end_pct = None
    elif input_method == "percentage":
        start_pct = float(request.form.get("start_pct") or 0)
        end_pct = float(request.form.get("end_pct") or 0)
        kwh_per_pct = float(request.form.get("kwh_per_pct") or get_setting(token, "kwh_per_pct", 2.05))
        kwh = round((end_pct - start_pct) * kwh_per_pct, 2)
    else:
        return redirect(url_for("index", token=token))

    if kwh <= 0:
        return redirect(url_for("index", token=token))

    vin = (request.form.get("vin") or "").strip().upper()

    conn = get_db()
    conn.execute(
        """INSERT INTO charges (session_token, date, kwh, start_pct, end_pct, input_method, vin)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (token, date_str, kwh, start_pct, end_pct, input_method, vin),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index", token=token))


@app.route("/s/<token>/delete/<int:charge_id>", methods=["POST"])
def delete_charge(token, charge_id):
    get_session_or_404(token)
    conn = get_db()
    conn.execute("DELETE FROM charges WHERE id = ? AND session_token = ?", (charge_id, token))
    conn.commit()
    conn.close()
    return redirect(url_for("index", token=token))


@app.route("/s/<token>/settings", methods=["POST"])
def update_settings(token):
    get_session_or_404(token)
    conn = get_db()
    for key in ("kwh_per_pct", "price_per_kwh"):
        val = request.form.get(key)
        if val:
            conn.execute(
                "INSERT OR REPLACE INTO settings (session_token, key, value) VALUES (?, ?, ?)",
                (token, key, str(float(val))),
            )
    conn.commit()
    conn.close()
    return redirect(url_for("index", token=token))


@app.route("/s/<token>/export")
def export_csv(token):
    get_session_or_404(token)
    conn = get_db()
    charges = conn.execute(
        "SELECT * FROM charges WHERE session_token = ? ORDER BY date DESC, id DESC",
        (token,),
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


@app.route("/s/<token>/report")
def report(token):
    get_session_or_404(token)
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    price_per_kwh = get_setting(token, "price_per_kwh", 0.35)

    conn = get_db()
    summary = []
    if date_from and date_to:
        rows = conn.execute(
            """SELECT vin, SUM(kwh) as total_kwh, COUNT(*) as sessions
               FROM charges
               WHERE session_token = ? AND date >= ? AND date <= ?
               GROUP BY vin
               ORDER BY vin""",
            (token, date_from, date_to),
        ).fetchall()
        for r in rows:
            summary.append({
                "vin": r["vin"] or "(no VIN)",
                "vin_raw": r["vin"],
                "total_kwh": round(r["total_kwh"], 2),
                "sessions": r["sessions"],
                "total_cost": round(r["total_kwh"] * price_per_kwh, 2),
            })

    conn.close()
    return render_template(
        "report.html",
        token=token,
        summary=summary,
        date_from=date_from,
        date_to=date_to,
        price_per_kwh=price_per_kwh,
    )


@app.route("/s/<token>/report/pdf")
def report_pdf(token):
    get_session_or_404(token)
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    vin = request.args.get("vin", "")
    price_per_kwh = get_setting(token, "price_per_kwh", 0.35)

    if not date_from or not date_to or not vin:
        return redirect(url_for("report", token=token))

    conn = get_db()
    charges = conn.execute(
        """SELECT * FROM charges
           WHERE session_token = ? AND date >= ? AND date <= ? AND vin = ?
           ORDER BY date ASC""",
        (token, date_from, date_to, vin),
    ).fetchall()
    conn.close()

    if not charges:
        return redirect(url_for("report", token=token, **{"from": date_from, "to": date_to}))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Charging Report", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "VIN: %s" % vin, ln=True)
    pdf.cell(0, 8, "Period: %s to %s" % (date_from, date_to), ln=True)
    pdf.cell(0, 8, "Price per kWh: %s" % price_per_kwh, ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(30, 8, "Date", border=1)
    pdf.cell(35, 8, "kWh", border=1, align="C")
    pdf.cell(35, 8, "Cost", border=1, align="C")
    pdf.cell(25, 8, "Start %", border=1, align="C")
    pdf.cell(25, 8, "End %", border=1, align="C")
    pdf.cell(30, 8, "Method", border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 10)
    total_kwh = 0
    total_cost = 0
    for c in charges:
        cost = round(c["kwh"] * price_per_kwh, 2)
        total_kwh += c["kwh"]
        total_cost += cost
        pdf.cell(30, 8, c["date"][:10], border=1)
        pdf.cell(35, 8, "%.2f" % c["kwh"], border=1, align="C")
        pdf.cell(35, 8, "%.2f" % cost, border=1, align="C")
        pdf.cell(25, 8, "%.0f" % c["start_pct"] if c["start_pct"] is not None else "-", border=1, align="C")
        pdf.cell(25, 8, "%.0f" % c["end_pct"] if c["end_pct"] is not None else "-", border=1, align="C")
        pdf.cell(30, 8, c["input_method"], border=1, align="C")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(30, 8, "TOTAL", border=1)
    pdf.cell(35, 8, "%.2f" % total_kwh, border=1, align="C")
    pdf.cell(35, 8, "%.2f" % total_cost, border=1, align="C")
    pdf.cell(80, 8, "", border=1)

    pdf_bytes = bytes(pdf.output())
    filename = "charging_%s_%s_to_%s.pdf" % (vin, date_from, date_to)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=%s" % filename},
    )


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
