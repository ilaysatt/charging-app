import csv
import io
import sqlite3
import os
import uuid
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, Response, send_from_directory
from fpdf import FPDF
from PIL import Image

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "charging.db")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


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
    # Add columns to existing databases
    for col, default in [("vin", "''"), ("photo_start", "''"), ("photo_end", "''")]:
        try:
            conn.execute("ALTER TABLE charges ADD COLUMN %s TEXT NOT NULL DEFAULT %s" % (col, default))
        except sqlite3.OperationalError:
            pass
    # Default kWh per percent - user can change in settings
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("kwh_per_pct", "2.05"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("price_per_kwh", "0.35"),
    )
    conn.commit()
    conn.close()


def get_setting(key, default):
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return float(row["value"]) if row else default


def get_kwh_per_pct():
    return get_setting("kwh_per_pct", 2.05)


def get_price_per_kwh():
    return get_setting("price_per_kwh", 0.35)


MAX_PHOTO_WIDTH = 1200


def save_photo(file_storage):
    """Save uploaded photo stripped of all metadata, resized. Returns filename or empty string."""
    if not file_storage or not file_storage.filename:
        return ""
    try:
        img = Image.open(file_storage)
        # Auto-rotate based on EXIF orientation before stripping
        img = Image.open(file_storage)
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
        # Create clean image (strips all metadata)
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        # Resize if too large
        if clean.width > MAX_PHOTO_WIDTH:
            ratio = MAX_PHOTO_WIDTH / clean.width
            clean = clean.resize((MAX_PHOTO_WIDTH, int(clean.height * ratio)), Image.LANCZOS)
        # Convert to RGB for JPEG
        if clean.mode != "RGB":
            clean = clean.convert("RGB")
        filename = "%s.jpg" % uuid.uuid4().hex[:12]
        clean.save(os.path.join(UPLOAD_DIR, filename), "JPEG", quality=75)
        return filename
    except Exception:
        return ""


def delete_photo(filename):
    if filename:
        path = os.path.join(UPLOAD_DIR, filename)
        if os.path.exists(path):
            os.remove(path)


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

    last_vin_row = conn.execute(
        "SELECT vin FROM charges ORDER BY id DESC LIMIT 1"
    ).fetchone()
    last_vin = last_vin_row["vin"] if last_vin_row else ""

    kwh_per_pct = get_kwh_per_pct()
    price_per_kwh = get_price_per_kwh()
    conn.close()
    return render_template(
        "index.html",
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
        last_vin=last_vin,
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
    if input_method == "percentage":
        photo_start = save_photo(request.files.get("photo_start_pct"))
    else:
        photo_start = save_photo(request.files.get("photo_start"))
    photo_end = save_photo(request.files.get("photo_end"))

    conn = get_db()
    conn.execute(
        """INSERT INTO charges (date, kwh, start_pct, end_pct, input_method, vin, photo_start, photo_end)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (date_str, kwh, start_pct, end_pct, input_method, vin, photo_start, photo_end),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/edit/<int:charge_id>", methods=["GET", "POST"])
def edit_charge(charge_id):
    conn = get_db()
    charge = conn.execute("SELECT * FROM charges WHERE id = ?", (charge_id,)).fetchone()
    if not charge:
        conn.close()
        return redirect(url_for("index"))

    if request.method == "POST":
        date_str = request.form.get("date") or charge["date"]
        vin = (request.form.get("vin") or "").strip().upper()

        # Handle photo uploads — keep existing if no new upload
        photo_start = charge["photo_start"]
        if request.files.get("photo_start") and request.files["photo_start"].filename:
            delete_photo(photo_start)
            photo_start = save_photo(request.files["photo_start"])
        if request.form.get("remove_photo_start"):
            delete_photo(photo_start)
            photo_start = ""

        photo_end = charge["photo_end"]
        if request.files.get("photo_end") and request.files["photo_end"].filename:
            delete_photo(photo_end)
            photo_end = save_photo(request.files["photo_end"])
        if request.form.get("remove_photo_end"):
            delete_photo(photo_end)
            photo_end = ""

        input_method = request.form.get("input_method") or charge["input_method"]
        if input_method == "kwh":
            kwh = float(request.form.get("kwh") or charge["kwh"])
            start_pct = None
            end_pct = None
        else:
            start_pct = float(request.form.get("start_pct") or 0)
            end_pct = float(request.form.get("end_pct") or 0)
            kwh_per_pct = float(request.form.get("kwh_per_pct") or get_kwh_per_pct())
            kwh = round((end_pct - start_pct) * kwh_per_pct, 2)

        conn.execute(
            """UPDATE charges SET date=?, kwh=?, start_pct=?, end_pct=?, input_method=?,
               vin=?, photo_start=?, photo_end=? WHERE id=?""",
            (date_str, kwh, start_pct, end_pct, input_method, vin, photo_start, photo_end, charge_id),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    kwh_per_pct = get_kwh_per_pct()
    conn.close()
    return render_template("edit.html", charge=charge, kwh_per_pct=kwh_per_pct)


@app.route("/delete/<int:charge_id>", methods=["POST"])
def delete_charge(charge_id):
    conn = get_db()
    charge = conn.execute("SELECT photo_start, photo_end FROM charges WHERE id = ?", (charge_id,)).fetchone()
    if charge:
        delete_photo(charge["photo_start"])
        delete_photo(charge["photo_end"])
    conn.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/settings", methods=["POST"])
def update_settings():
    conn = get_db()
    for key in ("kwh_per_pct", "price_per_kwh"):
        val = request.form.get(key)
        if val:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(float(val))),
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
    price_per_kwh = float(
        conn.execute("SELECT value FROM settings WHERE key='price_per_kwh'").fetchone()["value"]
    )
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "VIN", "kWh", "Cost", "Start %", "End %", "Input Method"])
    for c in charges:
        writer.writerow([
            c["date"],
            c["vin"],
            c["kwh"],
            round(c["kwh"] * price_per_kwh, 4),
            c["start_pct"] if c["start_pct"] is not None else "",
            c["end_pct"] if c["end_pct"] is not None else "",
            c["input_method"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=charges.csv"},
    )


@app.route("/report")
def report():
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    price_per_kwh = get_price_per_kwh()

    conn = get_db()
    vins = [r[0] for r in conn.execute(
        "SELECT DISTINCT vin FROM charges WHERE vin != '' ORDER BY vin"
    ).fetchall()]

    summary = []
    if date_from and date_to:
        rows = conn.execute(
            """SELECT vin, SUM(kwh) as total_kwh, COUNT(*) as sessions
               FROM charges
               WHERE date >= ? AND date <= ?
               GROUP BY vin
               ORDER BY vin""",
            (date_from, date_to),
        ).fetchall()
        for r in rows:
            summary.append({
                "vin": r["vin"] or "(no VIN)",
                "vin_raw": r["vin"],
                "total_kwh": round(r["total_kwh"], 2),
                "sessions": r["sessions"],
                "total_cost": round(r["total_kwh"] * price_per_kwh, 4),
            })

    conn.close()
    return render_template(
        "report.html",
        summary=summary,
        date_from=date_from,
        date_to=date_to,
        price_per_kwh=price_per_kwh,
    )


@app.route("/report/pdf")
def report_pdf():
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    vin = request.args.get("vin", "")
    price_per_kwh = get_price_per_kwh()

    if not date_from or not date_to or not vin:
        return redirect(url_for("report"))

    conn = get_db()
    charges = conn.execute(
        """SELECT * FROM charges
           WHERE date >= ? AND date <= ? AND vin = ?
           ORDER BY date ASC""",
        (date_from, date_to, vin),
    ).fetchall()
    conn.close()

    if not charges:
        return redirect(url_for("report", **{"from": date_from, "to": date_to}))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Charging Report", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "VIN: %s" % vin, ln=True)
    pdf.cell(0, 8, "Period: %s to %s" % (date_from, date_to), ln=True)
    pdf.cell(0, 8, "Price per kWh: %s" % price_per_kwh, ln=True)
    pdf.ln(5)

    # Data table header
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(10, 8, "#", border=1, align="C")
    pdf.cell(28, 8, "Date", border=1)
    pdf.cell(30, 8, "kWh", border=1, align="C")
    pdf.cell(30, 8, "Cost", border=1, align="C")
    pdf.cell(22, 8, "Start %", border=1, align="C")
    pdf.cell(22, 8, "End %", border=1, align="C")
    pdf.cell(28, 8, "Method", border=1, align="C")
    pdf.ln()

    # Create internal links for each row
    data_links = {}
    photo_links = {}
    for i in range(1, len(charges) + 1):
        data_links[i] = pdf.add_link()
        photo_links[i] = pdf.add_link()

    # Data table rows
    pdf.set_font("Helvetica", "", 10)
    total_kwh = 0
    total_cost = 0
    for i, c in enumerate(charges, 1):
        cost = round(c["kwh"] * price_per_kwh, 4)
        total_kwh += c["kwh"]
        total_cost += cost
        pdf.set_link(data_links[i], y=pdf.get_y(), page=pdf.page)
        has_photo = c["photo_start"] or c["photo_end"]
        if has_photo:
            pdf.set_text_color(0, 102, 204)
            pdf.cell(10, 8, str(i), border=1, align="C", link=photo_links[i])
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.cell(10, 8, str(i), border=1, align="C")
        pdf.cell(28, 8, c["date"][:10], border=1)
        pdf.cell(30, 8, "%.2f" % c["kwh"], border=1, align="C")
        pdf.cell(30, 8, "%.4f" % cost, border=1, align="C")
        pdf.cell(22, 8, "%.0f" % c["start_pct"] if c["start_pct"] is not None else "-", border=1, align="C")
        pdf.cell(22, 8, "%.0f" % c["end_pct"] if c["end_pct"] is not None else "-", border=1, align="C")
        pdf.cell(28, 8, c["input_method"], border=1, align="C")
        pdf.ln()

    # Totals row
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(10, 8, "", border=1)
    pdf.cell(28, 8, "TOTAL", border=1)
    pdf.cell(30, 8, "%.2f" % total_kwh, border=1, align="C")
    pdf.cell(30, 8, "%.4f" % total_cost, border=1, align="C")
    pdf.cell(72, 8, "", border=1)

    # Photos table
    photos_exist = any(c["photo_start"] or c["photo_end"] for c in charges)
    if photos_exist:
        pdf.ln(10)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Photos", ln=True)
        pdf.ln(3)

        img_w = 80
        for i, c in enumerate(charges, 1):
            if not c["photo_start"] and not c["photo_end"]:
                continue

            # Check if we need a new page (estimate ~60mm per photo row)
            if pdf.get_y() + 65 > pdf.h - pdf.b_margin:
                pdf.add_page()

            # Row label (links back to data row)
            pdf.set_link(photo_links[i], y=pdf.get_y(), page=pdf.page)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(0, 102, 204)
            pdf.cell(0, 7, "#%d - %s" % (i, c["date"][:10]), ln=True, link=data_links[i])
            pdf.set_text_color(0, 0, 0)

            row_y = pdf.get_y()
            max_h = 0

            # Start photo (left side)
            if c["photo_start"]:
                photo_path = os.path.join(UPLOAD_DIR, c["photo_start"])
                if os.path.exists(photo_path):
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.cell(img_w, 4, "Start", align="C")
                    pdf.ln(4)
                    img = Image.open(photo_path)
                    img_h = img_w * img.height / img.width
                    pdf.image(photo_path, x=pdf.l_margin, y=pdf.get_y(), w=img_w)
                    max_h = max(max_h, img_h)

            # End photo (right side)
            if c["photo_end"]:
                photo_path = os.path.join(UPLOAD_DIR, c["photo_end"])
                if os.path.exists(photo_path):
                    x_right = pdf.l_margin + img_w + 10
                    pdf.set_xy(x_right, row_y)
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.cell(img_w, 4, "End", align="C")
                    img = Image.open(photo_path)
                    img_h = img_w * img.height / img.width
                    pdf.image(photo_path, x=x_right, y=row_y + 4, w=img_w)
                    max_h = max(max_h, img_h)

            pdf.set_y(row_y + 4 + max_h + 5)

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
