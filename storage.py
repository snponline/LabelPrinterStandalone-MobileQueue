"""Local SQLite storage for drug label templates - no external database or
POS integration needed. Each installation keeps its own drug list."""
import json
import os
import sqlite3
from datetime import datetime

APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LabelPrinterStandalone_MobileQueue")
DB_PATH = os.path.join(APP_DATA_DIR, "data.db")

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS print_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT,
            customer_phone TEXT,
            drugs_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    return conn


def search_templates(term, limit=30):
    """Prefix-priority search over saved drug1 names (ชื่อการค้า)."""
    term = (term or "").strip()
    if not term:
        return []
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, drug1 FROM drug_templates
            WHERE drug1 LIKE ?
            ORDER BY CASE WHEN drug1 LIKE ? THEN 0 ELSE 1 END, drug1
            LIMIT ?
            """,
            (f"%{term}%", f"{term}%", limit),
        )
        return [{"idproduct": row[0], "name": row[1]} for row in cur.fetchall()]
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
            "SELECT drug1, drug2, note, qty, unit, per_day, every_hr, meal, times, extra_labels, usage_mode "
            "FROM drug_templates WHERE id = ?",
            (idproduct,),
        )
        row = cur.fetchone()
        if not row:
            return None
        drug1, drug2, note, qty, unit, per_day, every_hr, meal, times_json, extra_json, usage_mode = row
        return {
            "drug1": drug1, "drug2": drug2 or "", "note": note or "",
            "qty": qty or "", "unit": unit or "", "per_day": per_day or "",
            "every_hr": every_hr or "", "meal": meal or "",
            "times": json.loads(times_json) if times_json else [],
            "extra_labels": json.loads(extra_json) if extra_json else [],
            "usage_mode": usage_mode or "oral",
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
        now = datetime.now().isoformat()
        if idproduct:
            cur.execute(
                """
                UPDATE drug_templates SET
                    drug1 = ?, drug2 = ?, note = ?, qty = ?, unit = ?, per_day = ?,
                    every_hr = ?, meal = ?, times = ?, extra_labels = ?, usage_mode = ?, updated_at = ?
                WHERE id = ?
                """,
                (drug["drug1"], drug.get("drug2", ""), drug.get("note", ""), drug.get("qty", ""),
                 drug.get("unit", ""), drug.get("per_day", ""), drug.get("every_hr", ""),
                 drug.get("meal", ""), times_json, extra_json, usage_mode, now, idproduct),
            )
            row_id = idproduct
        else:
            cur.execute(
                """
                INSERT INTO drug_templates
                    (drug1, drug2, note, qty, unit, per_day, every_hr, meal, times, extra_labels, usage_mode, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (drug["drug1"], drug.get("drug2", ""), drug.get("note", ""), drug.get("qty", ""),
                 drug.get("unit", ""), drug.get("per_day", ""), drug.get("every_hr", ""),
                 drug.get("meal", ""), times_json, extra_json, usage_mode, now),
            )
            row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def bulk_import_names(names):
    """Create blank drug templates (ชื่อการค้า only, everything else empty)
    for an Excel import. Names that already exist are left untouched - this
    never overwrites dosing info a pharmacist already entered. Returns
    (imported, skipped_existing, skipped_blank)."""
    conn = _connect()
    imported = skipped_existing = skipped_blank = 0
    try:
        cur = conn.cursor()
        now = datetime.now().isoformat()
        seen_this_batch = set()
        for raw in names:
            name = (raw or "").strip()
            if not name or name in seen_this_batch:
                skipped_blank += 1
                continue
            seen_this_batch.add(name)
            cur.execute("SELECT id FROM drug_templates WHERE drug1 = ?", (name,))
            if cur.fetchone():
                skipped_existing += 1
                continue
            cur.execute(
                """
                INSERT INTO drug_templates
                    (drug1, drug2, note, qty, unit, per_day, every_hr, meal, times, extra_labels, updated_at)
                VALUES (?, '', '', '', '', '', '', '', '[]', '[]', ?)
                """,
                (name, now),
            )
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return imported, skipped_existing, skipped_blank


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

def add_queue_job(patient_name, customer_phone, drugs):
    conn = _connect()
    try:
        now = datetime.now().isoformat()
        drugs_json = json.dumps(drugs, ensure_ascii=False)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO print_queue (patient_name, customer_phone, drugs_json, submitted_at) VALUES (?, ?, ?, ?)",
            (patient_name or "", customer_phone or "", drugs_json, now),
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
            "SELECT id, patient_name, customer_phone, drugs_json, submitted_at FROM print_queue ORDER BY id"
        )
        out = []
        for id_, patient_name, customer_phone, drugs_json, submitted_at in cur.fetchall():
            try:
                drugs = json.loads(drugs_json)
            except Exception:
                drugs = []
            out.append({
                "id": id_, "patient_name": patient_name or "", "customer_phone": customer_phone or "",
                "drugs": drugs, "submitted_at": submitted_at,
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
            "SELECT patient_name, customer_phone, drugs_json FROM print_queue WHERE id = ?", (job_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("DELETE FROM print_queue WHERE id = ?", (job_id,))
        conn.commit()
        if cur.rowcount == 0:
            return None
        patient_name, customer_phone, drugs_json = row
        try:
            drugs = json.loads(drugs_json)
        except Exception:
            drugs = []
        return {"patient_name": patient_name or "", "customer_phone": customer_phone or "", "drugs": drugs}
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
