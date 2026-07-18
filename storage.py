"""Local SQLite storage for drug label templates - no external database or
POS integration needed. Each installation keeps its own drug list."""
import io
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta

from PIL import Image

APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LabelPrinterStandalone_MobileQueue")
DB_PATH = os.path.join(APP_DATA_DIR, "data.db")
PATIENT_DOCS_DIR = os.path.join(APP_DATA_DIR, "patient_docs")

TIME_OPTIONS = ["เช้า", "เที่ยง", "เย็น", "ก่อนนอน"]


def _connect():
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drug_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug1 TEXT UNIQUE NOT NULL,
            drug2 TEXT, note TEXT, qty TEXT, unit TEXT,
            per_day TEXT, every_hr TEXT, meal TEXT,
            times TEXT, extra_labels TEXT,
            updated_at TEXT
        )
    """)
    # usage_mode (กิน/ทา/หยอด) was added after the table already existed on
    # some installs - add it if missing rather than requiring a fresh DB.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(drug_templates)")}
    if "usage_mode" not in existing_cols:
        conn.execute("ALTER TABLE drug_templates ADD COLUMN usage_mode TEXT")
    # barcode - for shops with a barcode scanner attached (scanning is just
    # fast keystrokes + Enter, no special hardware handling needed) - lets
    # search_templates() match a scanned code the same way it matches a
    # typed name. Not unique-constrained: real-world data occasionally has
    # a shared/reused code, and a soft duplicate shouldn't block a save.
    if "barcode" not in existing_cols:
        conn.execute("ALTER TABLE drug_templates ADD COLUMN barcode TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drug_templates_barcode ON drug_templates(barcode)")
    # exp_date/label_qty remember the EXP and on-label quantity most
    # recently entered for this drug - a pharmacy dispenses from the same
    # lot/stock for a while, so these stay valid defaults across many
    # prints until the lot actually changes.
    if "exp_date" not in existing_cols:
        conn.execute("ALTER TABLE drug_templates ADD COLUMN exp_date TEXT")
    if "label_qty" not in existing_cols:
        conn.execute("ALTER TABLE drug_templates ADD COLUMN label_qty TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS print_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT,
            customer_phone TEXT,
            drugs_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        )
    """)
    existing_queue_cols = {row[1] for row in conn.execute("PRAGMA table_info(print_queue)")}
    if "has_allergy" not in existing_queue_cols:
        conn.execute("ALTER TABLE print_queue ADD COLUMN has_allergy INTEGER NOT NULL DEFAULT 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS print_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT,
            printed_at TEXT NOT NULL
        )
    """)
    # hidden (mark-as-read, doesn't delete) / customer_phone were added
    # after the table already existed on some installs - add if missing.
    existing_job_cols = {row[1] for row in conn.execute("PRAGMA table_info(print_jobs)")}
    if "hidden" not in existing_job_cols:
        conn.execute("ALTER TABLE print_jobs ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    if "customer_phone" not in existing_job_cols:
        conn.execute("ALTER TABLE print_jobs ADD COLUMN customer_phone TEXT")
    # archived is deliberately separate from hidden: hidden is the per-row
    # "mark as read" toggle (dims the row gray, stays in the list); archived
    # is the bulk "start a new day" action (removed from the default view
    # entirely, toggle-able back). Conflating the two made the per-row toggle
    # make rows vanish instead of just dimming - this column exists so both
    # can coexist without interfering with each other.
    if "archived" not in existing_job_cols:
        conn.execute("ALTER TABLE print_jobs ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
    # patient_id links a print job to a real patients-table record - only
    # ever set when one unambiguously exists (saved-to-patient-file at print
    # time, or picked from an existing profile) - see find_patient_id().
    # Deliberately nullable: most prints never touch the patients table at
    # all, and older rows from before this column existed have no way to
    # backfill it except a best-effort one-time script.
    if "patient_id" not in existing_job_cols:
        conn.execute("ALTER TABLE print_jobs ADD COLUMN patient_id INTEGER")
    # indexes for search-by-patient to stay fast even after years of history
    conn.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_patient ON print_jobs(patient_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_phone ON print_jobs(customer_phone)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_printed_at ON print_jobs(printed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_print_jobs_patient_id ON print_jobs(patient_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS print_job_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            idproduct INTEGER,
            drug1 TEXT NOT NULL,
            drug2 TEXT, note TEXT, qty TEXT, unit TEXT, per_day TEXT, every_hr TEXT, meal TEXT,
            times TEXT, extra_labels TEXT, usage_mode TEXT, print_qty INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            allergy_note TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # hn_code (YYYY-NNNNN, 5-digit running number reset per year) is a
    # customer-facing id for a planned future "online card" feature -
    # deliberately separate from the internal autoincrement `id` (which
    # stays a plain surrogate key for FK/index purposes) so its format can
    # change independently later without touching anything it's linked to.
    existing_patient_cols = {row[1] for row in conn.execute("PRAGMA table_info(patients)")}
    if "hn_code" not in existing_patient_cols:
        conn.execute("ALTER TABLE patients ADD COLUMN hn_code TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_patients_hn_code ON patients(hn_code)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patient_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            note TEXT,
            uploaded_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_patient_documents_patient ON patient_documents(patient_id)")
    return conn


def search_templates(term, limit=30):
    """Prefix-priority search over saved drug1 names (ชื่อการค้า) - also
    matches barcode, so scanning one (a barcode scanner is just a fast
    keyboard + Enter, nothing special to handle) surfaces the same result
    a manual name search would."""
    term = (term or "").strip()
    if not term:
        return []
    conn = _connect()
    try:
        cur = conn.cursor()
        like = f"%{term}%"
        cur.execute(
            """
            SELECT id, drug1 FROM drug_templates
            WHERE drug1 LIKE ? OR barcode LIKE ?
            ORDER BY CASE WHEN drug1 LIKE ? THEN 0 ELSE 1 END, drug1
            LIMIT ?
            """,
            (like, like, f"{term}%", limit),
        )
        return [{"idproduct": row[0], "name": row[1]} for row in cur.fetchall()]
    finally:
        conn.close()


def find_template_by_barcode(barcode):
    """Exact match only - used to auto-add a drug the instant a barcode is
    scanned into the search box (Enter key), skipping the usual
    double-click-a-result step. Returns None for no match OR more than one
    (an ambiguous scan shouldn't silently pick one)."""
    barcode = (barcode or "").strip()
    if not barcode:
        return None
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, drug1 FROM drug_templates WHERE barcode = ?", (barcode,)).fetchall()
        if len(rows) != 1:
            return None
        return {"idproduct": rows[0][0], "name": rows[0][1]}
    finally:
        conn.close()


def has_dosing_data(info):
    """A drug_templates row can exist with only drug1 filled in (e.g. from a
    bulk Excel import of names) - that's not the same as having real dosing
    info to show green/copy from. Check for actual content, not just
    row-exists. Shared between the desktop app and the mobile queue server
    so both agree on what counts as "has info"."""
    return bool(info) and any([
        info.get("drug2"), info.get("note"), info.get("qty"), info.get("per_day"),
        info.get("every_hr"), info.get("times"), info.get("extra_labels"),
    ])


def get_template(idproduct):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT drug1, drug2, note, qty, unit, per_day, every_hr, meal, times, extra_labels, usage_mode, barcode, "
            "exp_date, label_qty "
            "FROM drug_templates WHERE id = ?",
            (idproduct,),
        )
        row = cur.fetchone()
        if not row:
            return None
        (drug1, drug2, note, qty, unit, per_day, every_hr, meal, times_json, extra_json, usage_mode, barcode,
         exp_date, label_qty) = row
        return {
            "drug1": drug1, "drug2": drug2 or "", "note": note or "",
            "qty": qty or "", "unit": unit or "", "per_day": per_day or "",
            "every_hr": every_hr or "", "meal": meal or "",
            "times": json.loads(times_json) if times_json else [],
            "extra_labels": json.loads(extra_json) if extra_json else [],
            "usage_mode": usage_mode or "oral",
            "barcode": barcode or "",
            "exp_date": exp_date or "",
            "label_qty": label_qty or "",
        }
    finally:
        conn.close()


def upsert_template(idproduct, drug):
    """Insert or update a drug template. idproduct is the row id (None for a
    brand-new drug that hasn't been saved yet - a new row is created and its
    id returned). drug1 (ชื่อการค้า) is UNIQUE but is NOT the key we match on,
    so renaming an already-saved drug's trade name updates the same row
    instead of creating a duplicate."""
    conn = _connect()
    try:
        cur = conn.cursor()
        times_json = json.dumps(drug.get("times") or [], ensure_ascii=False)
        extra_json = json.dumps(drug.get("extra_labels") or [], ensure_ascii=False)
        usage_mode = drug.get("usage_mode", "oral")
        barcode = (drug.get("barcode") or "").strip()
        exp_date = (drug.get("exp_date") or "").strip()
        label_qty = (drug.get("label_qty") or "").strip()
        now = datetime.now().isoformat()
        if idproduct:
            cur.execute(
                """
                UPDATE drug_templates SET
                    drug1 = ?, drug2 = ?, note = ?, qty = ?, unit = ?, per_day = ?,
                    every_hr = ?, meal = ?, times = ?, extra_labels = ?, usage_mode = ?, barcode = ?,
                    exp_date = ?, label_qty = ?, updated_at = ?
                WHERE id = ?
                """,
                (drug["drug1"], drug.get("drug2", ""), drug.get("note", ""), drug.get("qty", ""),
                 drug.get("unit", ""), drug.get("per_day", ""), drug.get("every_hr", ""),
                 drug.get("meal", ""), times_json, extra_json, usage_mode, barcode,
                 exp_date, label_qty, now, idproduct),
            )
            row_id = idproduct
        else:
            cur.execute(
                """
                INSERT INTO drug_templates
                    (drug1, drug2, note, qty, unit, per_day, every_hr, meal, times, extra_labels, usage_mode, barcode,
                     exp_date, label_qty, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (drug["drug1"], drug.get("drug2", ""), drug.get("note", ""), drug.get("qty", ""),
                 drug.get("unit", ""), drug.get("per_day", ""), drug.get("every_hr", ""),
                 drug.get("meal", ""), times_json, extra_json, usage_mode, barcode,
                 exp_date, label_qty, now),
            )
            row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def bulk_import_names_and_barcodes(rows):
    """rows: list of (name, barcode, generic_name) tuples from an Excel
    import (barcode/generic_name may be blank per-row, or the whole column
    may be absent - see read_excel_drug_names_and_barcodes()). Handles two
    cases with one code path, since they're really the same operation: (1) a
    fresh import where most names are brand new - creates a template with
    drug2 (generic name) prefilled if given; (2) adding barcodes/generic
    names to drugs that already exist and already have dosing filled in -
    touches ONLY the barcode/drug2 columns on a name match, never overwrites
    qty/per_day/note/etc. A literal "NT" generic-name value (a real
    not-applicable placeholder seen in at least one shop's POS export, e.g.
    for non-drug items like bandages) is treated as blank rather than
    imported as visible label text. Returns
    (created, updated_barcode, skipped_blank)."""
    conn = _connect()
    created = updated_barcode = skipped_blank = 0
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat()
        seen_this_batch = set()
        for row in rows:
            raw_name, raw_barcode, raw_generic = (list(row) + ["", "", ""])[:3]
            name = (raw_name or "").strip()
            barcode = (raw_barcode or "").strip()
            generic = (raw_generic or "").strip()
            if generic.upper() == "NT":
                generic = ""
            if not name or name in seen_this_batch:
                skipped_blank += 1
                continue
            seen_this_batch.add(name)
            cur.execute("SELECT id FROM drug_templates WHERE drug1 = ?", (name,))
            existing = cur.fetchone()
            if existing:
                if barcode or generic:
                    sets, params = [], []
                    if barcode:
                        sets.append("barcode = ?")
                        params.append(barcode)
                    if generic:
                        sets.append("drug2 = ?")
                        params.append(generic)
                    params.append(existing[0])
                    cur.execute(f"UPDATE drug_templates SET {', '.join(sets)} WHERE id = ?", params)
                    updated_barcode += 1
            else:
                cur.execute(
                    """
                    INSERT INTO drug_templates
                        (drug1, drug2, note, qty, unit, per_day, every_hr, meal, times, extra_labels, barcode, updated_at)
                    VALUES (?, ?, '', '', '', '', '', '', '[]', '[]', ?, ?)
                    """,
                    (name, generic, barcode, now),
                )
                created += 1
        conn.commit()
    finally:
        conn.close()
    return created, updated_barcode, skipped_blank


def delete_template(idproduct):
    """Delete one drug template by row id."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM drug_templates WHERE id = ?", (idproduct,))
        conn.commit()
    finally:
        conn.close()


def count_templates():
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM drug_templates").fetchone()[0]
    finally:
        conn.close()


def list_all_template_names():
    """Every saved drug's trade name (ชื่อการค้า), alphabetical - for the
    "แสดงยาทั้งหมด" button in ⚙️ ตั้งค่า, so a pharmacist can sanity-check
    what's actually in this machine's local DB (e.g. before/after an Excel
    import or a ล้าง DB) without needing to search one name at a time."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT drug1 FROM drug_templates ORDER BY drug1 COLLATE NOCASE").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def clear_all_templates():
    """Delete every drug template - e.g. to redo a bad Excel import from
    scratch. Returns the number of rows removed."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM drug_templates")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── Mobile print queue (staff phones submit, the one PC/printer claims+prints) ──

def add_queue_job(patient_name, customer_phone, drugs, has_allergy=False):
    conn = _connect()
    try:
        now = datetime.now().isoformat()
        drugs_json = json.dumps(drugs, ensure_ascii=False)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO print_queue (patient_name, customer_phone, drugs_json, submitted_at, has_allergy) "
            "VALUES (?, ?, ?, ?, ?)",
            (patient_name or "", customer_phone or "", drugs_json, now, 1 if has_allergy else 0),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_queue_jobs():
    """Pending jobs, oldest first, each with drugs already parsed back to a list."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, patient_name, customer_phone, drugs_json, submitted_at, has_allergy FROM print_queue ORDER BY id"
        )
        out = []
        for id_, patient_name, customer_phone, drugs_json, submitted_at, has_allergy in cur.fetchall():
            try:
                drugs = json.loads(drugs_json)
            except Exception:
                drugs = []
            out.append({
                "id": id_, "patient_name": patient_name or "", "customer_phone": customer_phone or "",
                "drugs": drugs, "submitted_at": submitted_at, "has_allergy": bool(has_allergy),
            })
        return out
    finally:
        conn.close()


def claim_queue_job(job_id):
    """Delete-then-return (claim-then-print pattern, same as the shop POS
    version's mobile queue) - returns None if the job is already gone (e.g.
    claimed a moment ago). Not a real race concern with one PC/one printer,
    but costs nothing to do the safe way."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT patient_name, customer_phone, drugs_json, has_allergy FROM print_queue WHERE id = ?", (job_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("DELETE FROM print_queue WHERE id = ?", (job_id,))
        conn.commit()
        if cur.rowcount == 0:
            return None
        patient_name, customer_phone, drugs_json, has_allergy = row
        try:
            drugs = json.loads(drugs_json)
        except Exception:
            drugs = []
        return {
            "patient_name": patient_name or "", "customer_phone": customer_phone or "", "drugs": drugs,
            "has_allergy": bool(has_allergy),
        }
    finally:
        conn.close()


def count_queue_jobs():
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM print_queue").fetchone()[0]
    finally:
        conn.close()


# ── Staff names (for the mobile queue page's "who's submitting" picker) ──

def list_staff_names():
    conn = _connect()
    try:
        cur = conn.execute("SELECT id, name FROM staff_names ORDER BY name")
        return [{"id": id_, "name": name} for id_, name in cur.fetchall()]
    finally:
        conn.close()


def add_staff_name(name):
    name = (name or "").strip()
    if not name:
        return None
    conn = _connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO staff_names (name) VALUES (?)", (name,))
        except sqlite3.IntegrityError:
            # already exists - not an error, just return the existing row's id
            row = conn.execute("SELECT id FROM staff_names WHERE name = ?", (name,)).fetchone()
            return row[0] if row else None
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_staff_name(staff_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM staff_names WHERE id = ?", (staff_id,))
        conn.commit()
    finally:
        conn.close()


# ── Dispensing history (แฟ้มประวัติการจ่ายยา - kept permanently, never
# auto-deleted) ──
# Grouped by "job" (one print-confirm action = one customer's whole order),
# not a flat per-drug list - staff need to see everything a customer got in
# one visit together, re-check dosing per drug, reprint the whole order
# again in one click, and search back by patient name/phone at any time in
# the future (years later). At realistic pharmacy volumes this stays well
# under ~1GB even over 10 years - no need to ever purge or export to a
# separate file; indexes on patient_name/customer_phone/printed_at keep
# search fast as it grows.

def add_print_job(patient_name, customer_phone, drugs, patient_id=None):
    """`drugs` is the same list of dicts already used everywhere else in this
    app (selected_drugs) - idproduct, drug1, drug2, note, qty, unit, per_day,
    every_hr, meal, times, extra_labels, usage_mode, print_qty. patient_name
    and customer_phone are both optional (blank string if not given).
    patient_id is only ever passed when a real patients-table record
    unambiguously exists for this print (see find_or_create_patient/
    find_patient_id) - most prints leave it None."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO print_jobs (patient_name, customer_phone, printed_at, patient_id) VALUES (?, ?, ?, ?)",
            (patient_name or "", customer_phone or "", datetime.now().isoformat(), patient_id),
        )
        job_id = cur.lastrowid
        for d in drugs:
            cur.execute(
                """
                INSERT INTO print_job_items
                    (job_id, idproduct, drug1, drug2, note, qty, unit, per_day, every_hr, meal,
                     times, extra_labels, usage_mode, print_qty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, d.get("idproduct"), d.get("drug1", ""), d.get("drug2", ""),
                    d.get("note", ""), d.get("qty", ""), d.get("unit", ""), d.get("per_day", ""),
                    d.get("every_hr", ""), d.get("meal", ""),
                    json.dumps(d.get("times") or [], ensure_ascii=False),
                    json.dumps(d.get("extra_labels") or [], ensure_ascii=False),
                    d.get("usage_mode", "oral"), d.get("print_qty", 1),
                ),
            )
        conn.commit()
        return job_id
    finally:
        conn.close()


def _rows_to_jobs(conn, job_rows):
    jobs = []
    for job_id, patient_name, customer_phone, printed_at, hidden, archived, patient_id in job_rows:
        item_rows = conn.execute(
            """
            SELECT idproduct, drug1, drug2, note, qty, unit, per_day, every_hr, meal,
                   times, extra_labels, usage_mode, print_qty
            FROM print_job_items WHERE job_id = ?
            """,
            (job_id,),
        ).fetchall()
        drugs = []
        for (idproduct, drug1, drug2, note, qty, unit, per_day, every_hr, meal,
             times_json, extra_json, usage_mode, print_qty) in item_rows:
            drugs.append({
                "idproduct": idproduct, "drug1": drug1, "drug2": drug2 or "", "note": note or "",
                "qty": qty or "", "unit": unit or "", "per_day": per_day or "",
                "every_hr": every_hr or "", "meal": meal or "",
                "times": json.loads(times_json) if times_json else [],
                "extra_labels": json.loads(extra_json) if extra_json else [],
                "usage_mode": usage_mode or "oral", "print_qty": print_qty or 1,
            })
        jobs.append({
            "id": job_id, "patient_name": patient_name or "", "customer_phone": customer_phone or "",
            "printed_at": printed_at, "hidden": bool(hidden), "archived": bool(archived),
            "patient_id": patient_id, "drugs": drugs,
        })
    return jobs


def list_print_jobs(hours=24):
    """Most recent first, each with its full drug list already parsed back
    into dicts - only jobs printed within the last `hours`. Pass hours=None
    for the entire history (use search_print_jobs() instead when possible -
    unbounded loads get slow after years of data)."""
    conn = _connect()
    try:
        if hours is None:
            job_rows = conn.execute(
                "SELECT id, patient_name, customer_phone, printed_at, hidden, archived, patient_id "
                "FROM print_jobs ORDER BY printed_at DESC"
            ).fetchall()
        else:
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            job_rows = conn.execute(
                "SELECT id, patient_name, customer_phone, printed_at, hidden, archived, patient_id "
                "FROM print_jobs WHERE printed_at >= ? ORDER BY printed_at DESC",
                (cutoff,),
            ).fetchall()
        return _rows_to_jobs(conn, job_rows)
    finally:
        conn.close()


def search_print_jobs(term, limit=200):
    """Search the ENTIRE history (no time cutoff) by patient name or phone -
    for "have we dispensed anything to this person before, and what/when".
    Most recent first, capped at `limit` results."""
    conn = _connect()
    try:
        like = f"%{term}%"
        job_rows = conn.execute(
            """
            SELECT id, patient_name, customer_phone, printed_at, hidden, archived, patient_id FROM print_jobs
            WHERE patient_name LIKE ? OR customer_phone LIKE ?
            ORDER BY printed_at DESC LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        return _rows_to_jobs(conn, job_rows)
    finally:
        conn.close()


def list_print_jobs_for_patient(name, phone, patient_id=None, limit=500):
    """Exact match, for a patient profile view where we already know exactly
    who we're looking at and want precisely their history, not a fuzzy
    search. Prefers patient_id (unambiguous) when given, but still ORs in
    the legacy name/phone match so history recorded before patient_id
    existed - or from a print that was never linked to a saved profile -
    keeps showing up."""
    conn = _connect()
    try:
        conditions = []
        params = []
        if patient_id:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if name:
            conditions.append("patient_name = ?")
            params.append(name)
        if phone:
            conditions.append("customer_phone = ?")
            params.append(phone)
        if not conditions:
            return []
        where = " OR ".join(conditions)
        job_rows = conn.execute(
            f"SELECT id, patient_name, customer_phone, printed_at, hidden, archived, patient_id FROM print_jobs "
            f"WHERE {where} ORDER BY printed_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return _rows_to_jobs(conn, job_rows)
    finally:
        conn.close()


def set_print_job_hidden(job_id, hidden):
    """Mark-as-read/unread - does NOT delete anything, just dims it in the
    UI so staff can tell what they've already gone through."""
    conn = _connect()
    try:
        conn.execute("UPDATE print_jobs SET hidden = ? WHERE id = ?", (1 if hidden else 0, job_id))
        conn.commit()
    finally:
        conn.close()


def delete_print_job(job_id):
    """Permanent delete - unlike set_print_job_hidden(), this actually
    removes the job and its items from the database."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM print_job_items WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM print_jobs WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def set_all_print_jobs_archived(archived):
    """Bulk archive/un-archive every job - deliberately a SEPARATE flag from
    `hidden` (the per-row "mark as read" toggle, which only dims a row gray
    and must never make it vanish from the list). Archiving clears the
    default 24-hour view for a new day; un-archiving is the toggle-back in
    case that was clicked by mistake or the day's entries need a second
    look. Does NOT delete anything - full history stays permanently
    searchable via search_print_jobs() regardless of archived state."""
    conn = _connect()
    try:
        conn.execute("UPDATE print_jobs SET archived = ?", (1 if archived else 0,))
        conn.commit()
    finally:
        conn.close()


# ── Patients (allergy notes + supporting documents, searchable by name/phone) ──
# Deliberately NOT foreign-keyed to print_jobs - staff type a free-text name/
# phone at print time same as always (no forced "pick a patient" step), and
# a patient's purchase history is just print_jobs matched by that same name/
# phone at view time (reuses search_print_jobs()). This table only exists to
# hold the extra stuff a loose name/phone string can't: allergy notes and
# uploaded documents.

def search_patients(term, limit=50):
    """Prefix-priority, same convention as search_templates()/search_print_jobs
    peers - a name/phone that *starts with* the typed term ranks above one
    that merely contains it, so "รักดี" surfaces someone actually named that
    before someone whose note happens to mention it elsewhere."""
    term = (term or "").strip()
    if not term:
        return []
    conn = _connect()
    try:
        like = f"%{term}%"
        prefix_like = f"{term}%"
        rows = conn.execute(
            "SELECT id, name, phone, allergy_note FROM patients "
            "WHERE name LIKE ? OR phone LIKE ? "
            "ORDER BY CASE WHEN name LIKE ? OR phone LIKE ? THEN 0 ELSE 1 END, name LIMIT ?",
            (like, like, prefix_like, prefix_like, limit),
        ).fetchall()
        return [{"id": r[0], "name": r[1], "phone": r[2] or "", "allergy_note": r[3] or ""} for r in rows]
    finally:
        conn.close()


def get_patient(patient_id):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, name, phone, allergy_note, hn_code FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "phone": row[2] or "", "allergy_note": row[3] or "", "hn_code": row[4] or ""}
    finally:
        conn.close()


def list_all_patients(order_by="name"):
    """Every patient record, for the "HN ทั้งหมด" management list - not
    filtered by search term like search_patients(). order_by is whitelisted
    (never interpolate a caller-supplied column name into SQL)."""
    column = "hn_code" if order_by == "hn_code" else "name"
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT id, name, phone, allergy_note, hn_code FROM patients ORDER BY {column}"
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "phone": r[2] or "", "allergy_note": r[3] or "", "hn_code": r[4] or ""}
            for r in rows
        ]
    finally:
        conn.close()


def delete_patient(patient_id):
    """Removes the patient record, their uploaded documents (files + rows),
    and unlinks (not deletes) any print_jobs that pointed at this patient_id
    - the print history itself (name/phone/drugs snapshot) is left intact,
    only the FK link is cleared, same principle as archiving vs. deleting a
    print job elsewhere in this app."""
    patient_dir = os.path.join(PATIENT_DOCS_DIR, str(patient_id))
    if os.path.isdir(patient_dir):
        shutil.rmtree(patient_dir, ignore_errors=True)
    conn = _connect()
    try:
        conn.execute("DELETE FROM patient_documents WHERE patient_id = ?", (patient_id,))
        conn.execute("UPDATE print_jobs SET patient_id = NULL WHERE patient_id = ?", (patient_id,))
        conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        conn.commit()
    finally:
        conn.close()


def delete_all_patients():
    """Wipes every patient record, all their documents, and unlinks every
    print_jobs.patient_id - for a store that just started using patient
    profiles and wants to reset (e.g. test/dummy entries from trying the
    feature out). Print history text (names/phones/drugs) is untouched;
    only the patients table and its FK links are cleared. hn_code numbering
    naturally restarts at 00001 next time a patient is created, since
    _generate_hn_code() looks at what's actually in the (now empty) table."""
    if os.path.isdir(PATIENT_DOCS_DIR):
        shutil.rmtree(PATIENT_DOCS_DIR, ignore_errors=True)
    conn = _connect()
    try:
        conn.execute("DELETE FROM patient_documents")
        conn.execute("UPDATE print_jobs SET patient_id = NULL")
        conn.execute("DELETE FROM patients")
        conn.commit()
    finally:
        conn.close()


def _generate_hn_code(conn, date=None):
    """YYMMDD-NNNNN (Christian-era year/month/day, 2-digit year), 5-digit
    running number that resets each day. Computed from the max existing
    suffix for that day rather than a row COUNT, so a deleted patient
    record never causes a code to be reused."""
    date = date or datetime.now()
    prefix = date.strftime("%y%m%d") + "-"
    rows = conn.execute("SELECT hn_code FROM patients WHERE hn_code LIKE ?", (prefix + "%",)).fetchall()
    max_n = 0
    for (code,) in rows:
        try:
            max_n = max(max_n, int(code.split("-", 1)[1]))
        except (ValueError, IndexError, AttributeError):
            continue
    return f"{prefix}{max_n + 1:05d}"


def find_or_create_patient(name, phone):
    """Match on (name, phone) exactly - two different people who happen to
    share a name but not a phone number get separate records. Returns the
    patient id either way."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name and not phone:
        return None
    conn = _connect()
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT id FROM patients WHERE name = ? AND IFNULL(phone, '') = ?", (name, phone)
        ).fetchone()
        if row:
            return row[0]
        hn_code = _generate_hn_code(conn)
        cur.execute(
            "INSERT INTO patients (name, phone, allergy_note, created_at, hn_code) VALUES (?, ?, '', ?, ?)",
            (name, phone, datetime.now().isoformat(), hn_code),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def find_patient_id(name, phone):
    """Like find_or_create_patient's lookup half, but never creates - for
    linking a print job to a patient record only when one unambiguously
    already exists (e.g. picking a name from print history), so reprinting
    an old anonymous/unsaved label never spawns a junk patient profile."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name and not phone:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id FROM patients WHERE name = ? AND IFNULL(phone, '') = ?", (name, phone)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def backfill_patient_hn_codes():
    """One-time (but safe to re-run - only touches rows still missing a
    code): assigns hn_code to patients created before this column existed.
    Processed oldest-first so earlier customers get the lower running
    numbers within their creation day, same as if they'd gotten a code the
    day they were first added."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, created_at FROM patients WHERE hn_code IS NULL ORDER BY created_at ASC"
        ).fetchall()
        for patient_id, created_at in rows:
            try:
                created_dt = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                created_dt = datetime.now()
            code = _generate_hn_code(conn, date=created_dt)
            conn.execute("UPDATE patients SET hn_code = ? WHERE id = ?", (code, patient_id))
            conn.commit()  # commit per-row so the next _generate_hn_code call sees this one
        return len(rows)
    finally:
        conn.close()


def backfill_print_job_patient_ids():
    """One-time (safe to re-run): for print_jobs still missing patient_id,
    link it up only where (name, phone) matches an existing patients row
    exactly - never creates a new patient record, and silently skips rows
    that don't match anything (most won't, since most prints are never
    saved to a patient file). Returns (linked_count, checked_count)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, patient_name, customer_phone FROM print_jobs WHERE patient_id IS NULL"
        ).fetchall()
        linked = 0
        for job_id, name, phone in rows:
            match = conn.execute(
                "SELECT id FROM patients WHERE name = ? AND IFNULL(phone, '') = ?",
                (name or "", phone or ""),
            ).fetchone()
            if match:
                conn.execute("UPDATE print_jobs SET patient_id = ? WHERE id = ?", (match[0], job_id))
                linked += 1
        conn.commit()
        return linked, len(rows)
    finally:
        conn.close()


def find_patients_by_exact_name(name):
    """Existing records with this exact name (any phone) - used at print time
    to warn the pharmacist when saving to a patient file would be ambiguous
    (two different people can share a name; phone is what tells them apart)."""
    name = (name or "").strip()
    if not name:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, IFNULL(phone, '') FROM patients WHERE name = ?", (name,)
        ).fetchall()
        return [{"id": r[0], "phone": r[1]} for r in rows]
    finally:
        conn.close()


def update_patient_allergy(patient_id, allergy_note):
    conn = _connect()
    try:
        conn.execute("UPDATE patients SET allergy_note = ? WHERE id = ?", (allergy_note or "", patient_id))
        conn.commit()
    finally:
        conn.close()


def _resize_image_bytes(image_bytes, max_side=800):
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def add_patient_document(patient_id, image_bytes, note):
    """Resizes to at most 800px on the longest side before saving (storage
    space, not just display) - documents are stored as files on disk under
    PATIENT_DOCS_DIR, not as DB blobs, so the sqlite file itself stays small
    and the mobile page can serve them as plain static files."""
    patient_dir = os.path.join(PATIENT_DOCS_DIR, str(patient_id))
    os.makedirs(patient_dir, exist_ok=True)
    resized = _resize_image_bytes(image_bytes)
    filename = f"{uuid.uuid4().hex}.jpg"
    with open(os.path.join(patient_dir, filename), "wb") as f:
        f.write(resized)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO patient_documents (patient_id, image_path, note, uploaded_at) VALUES (?, ?, ?, ?)",
            (patient_id, filename, note or "", datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def list_patient_documents(patient_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, image_path, note, uploaded_at FROM patient_documents "
            "WHERE patient_id = ? ORDER BY uploaded_at DESC",
            (patient_id,),
        ).fetchall()
        return [
            {
                "id": r[0], "image_path": r[1], "note": r[2] or "", "uploaded_at": r[3],
                "full_path": os.path.join(PATIENT_DOCS_DIR, str(patient_id), r[1]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_patient_document(doc_id):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT patient_id, image_path FROM patient_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row:
            patient_id, image_path = row
            full_path = os.path.join(PATIENT_DOCS_DIR, str(patient_id), image_path)
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
            except OSError:
                pass
            conn.execute("DELETE FROM patient_documents WHERE id = ?", (doc_id,))
            conn.commit()
    finally:
        conn.close()




