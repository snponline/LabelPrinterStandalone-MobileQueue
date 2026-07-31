"""Local SQLite storage for drug label templates - no external database or
POS integration needed. Each installation keeps its own drug list."""
import io
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, date as date_cls

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
    # Cached Grok (xAI) translations of "note" (Indication), generated on
    # demand when previewing/printing an English/Burmese label - see
    # translate_note_via_grok() in label_gui.py. Cleared whenever the Thai
    # source text is edited, so a stale translation can never survive an edit.
    if "note_en" not in existing_cols:
        conn.execute("ALTER TABLE drug_templates ADD COLUMN note_en TEXT")
    if "note_mm" not in existing_cols:
        conn.execute("ALTER TABLE drug_templates ADD COLUMN note_mm TEXT")
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
    # Device warranties (ประกันอุปกรณ์) — same concept as HOPE label_printer's
    # Label_Warranties, but local SQLite so this standalone build stays
    # independent of shop POS/SQL Server. patient_id FK → patients.id.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS warranties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            seller TEXT,
            seller_phone TEXT,
            price REAL,
            purchase_date TEXT,
            warranty_years REAL,
            expiry_date TEXT,
            note TEXT,
            external_id TEXT,
            source TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_warranties_patient ON warranties(patient_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_warranties_product ON warranties(product_name)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_warranties_external "
        "ON warranties(external_id) WHERE external_id IS NOT NULL AND external_id != ''"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patient_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            image_path TEXT,
            note TEXT,
            uploaded_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_patient_documents_patient ON patient_documents(patient_id)")
    # body_text - for saving a text note or an AI/patient chat transcript
    # (title + body_text, no image needed), not just a photo + short
    # caption. "note" is reused as the short title shown in the document
    # list. image_path was NOT NULL originally - existing installs need it
    # relaxed to allow text-only documents (SQLite can't ALTER a column's
    # NOT NULL constraint directly, so this rebuilds the table).
    existing_doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(patient_documents)")}
    if "body_text" not in existing_doc_cols:
        conn.execute("ALTER TABLE patient_documents ADD COLUMN body_text TEXT")
    image_path_col = next(
        (row for row in conn.execute("PRAGMA table_info(patient_documents)") if row[1] == "image_path"), None
    )
    if image_path_col is not None and image_path_col[3]:  # notnull flag
        # DROP+INSERT+RENAME below is a real transaction (unlike the
        # CREATE/ALTER/INDEX statements elsewhere in this function, which
        # auto-commit individually) - must be committed explicitly, and a
        # leftover _new table from a previous run that died mid-migration
        # (e.g. process killed before the commit) must be cleared first so
        # this doesn't crash with "table already exists" on retry.
        conn.execute("DROP TABLE IF EXISTS patient_documents_new")
        conn.execute("""
            CREATE TABLE patient_documents_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                image_path TEXT,
                note TEXT,
                uploaded_at TEXT NOT NULL,
                body_text TEXT
            )
        """)
        conn.execute(
            "INSERT INTO patient_documents_new (id, patient_id, image_path, note, uploaded_at, body_text) "
            "SELECT id, patient_id, image_path, note, uploaded_at, body_text FROM patient_documents"
        )
        conn.execute("DROP TABLE patient_documents")
        conn.execute("ALTER TABLE patient_documents_new RENAME TO patient_documents")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_patient_documents_patient ON patient_documents(patient_id)")
        conn.commit()

    # ── ข.ย.9 / ข.ย.11 ledgers ───────────────────────────────────────────
    # Same two ledgers as HOPE label_printer's Label_Purchase_Lots /
    # Label_Controlled_Sales, in local SQLite so this standalone build needs
    # no shop POS. The one structural difference: HOPE keys everything to the
    # POS catalog's idproduct, which doesn't exist here, so lots and sales
    # point at drug_templates.id instead.
    if "drug_report_category" not in existing_cols:
        # "none" | "dangerous" (ยาอันตรายที่ อย. กำหนด) | "tramadol"
        conn.execute("ALTER TABLE drug_templates ADD COLUMN drug_report_category TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchase_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            drug_name TEXT NOT NULL,
            received_date TEXT NOT NULL,
            source_company TEXT,
            lot_number TEXT,
            exp_date TEXT,
            unit_name TEXT,
            qty_received REAL NOT NULL,
            qty_remaining REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_lots_template ON purchase_lots(template_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_lots_received ON purchase_lots(received_date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS controlled_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            drug_name TEXT NOT NULL,
            lot_id INTEGER,
            lot_number TEXT,
            unit_name TEXT,
            qty REAL NOT NULL,
            category TEXT NOT NULL,
            buyer_name TEXT,
            buyer_citizen_id TEXT,
            buyer_address TEXT,
            info_complete INTEGER NOT NULL DEFAULT 0,
            sold_at TEXT NOT NULL,
            print_job_id INTEGER,
            patient_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_controlled_sales_template ON controlled_sales(template_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_controlled_sales_category ON controlled_sales(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_controlled_sales_complete ON controlled_sales(info_complete)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_controlled_sales_patient ON controlled_sales(patient_id)")
    # ใบส่งต่อผู้ป่วย (PhRF) - kept so the shop can reprint at the annual
    # inspection instead of depending on the paper copy alone.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            patient_name TEXT NOT NULL,
            gender TEXT, age TEXT, citizen_id TEXT, address TEXT, phone TEXT,
            right_type TEXT, right_other TEXT, hospital TEXT,
            reason_further INTEGER NOT NULL DEFAULT 0,
            reason_drp INTEGER NOT NULL DEFAULT 0,
            reason_followup INTEGER NOT NULL DEFAULT 0,
            med_review TEXT, chief_complaint TEXT, illness_history TEXT,
            chronic_disease TEXT, allergy_history TEXT, extra_info TEXT,
            problem_found TEXT,
            act_treat INTEGER NOT NULL DEFAULT 0, act_treat_text TEXT,
            act_advice INTEGER NOT NULL DEFAULT 0, act_advice_text TEXT,
            act_other INTEGER NOT NULL DEFAULT 0, act_other_text TEXT,
            pharmacist_name TEXT, license_no TEXT,
            referred_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_name ON referrals(patient_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_date ON referrals(referred_at)")
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
            "exp_date, label_qty, note_en, note_mm "
            "FROM drug_templates WHERE id = ?",
            (idproduct,),
        )
        row = cur.fetchone()
        if not row:
            return None
        (drug1, drug2, note, qty, unit, per_day, every_hr, meal, times_json, extra_json, usage_mode, barcode,
         exp_date, label_qty, note_en, note_mm) = row
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
            "note_en": note_en or "",
            "note_mm": note_mm or "",
        }
    finally:
        conn.close()


def save_note_translation(idproduct, lang, text):
    """Cache a Grok-translated Indication/note into drug_templates.note_en
    or note_mm, so it's reused next time this drug is picked instead of
    re-calling the API. Only UPDATEs an existing row (idproduct is the
    local row id here) - a brand-new, never-saved drug has no row to
    attach the translation to yet, and the translation is simply not
    cached in that case (re-translated next time)."""
    if lang not in ("en", "mm") or not idproduct:
        return
    conn = _connect()
    try:
        col = "note_en" if lang == "en" else "note_mm"
        conn.execute(f"UPDATE drug_templates SET {col} = ? WHERE id = ?", (text, idproduct))
        conn.commit()
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


def search_patient_name_suggestions(term, limit=10):
    """Prefix/contains match across BOTH patients (ประวัติผู้ป่วย) and
    print_jobs (ประวัติการจ่ายยา) - a person may exist in one but not the
    other (printed without ever being explicitly saved to a patient
    profile, or a patient profile created before any print). Used for the
    live autocomplete suggestions in open_patient_dialog()'s name entry, so
    typing a few letters is enough instead of the full name every time."""
    term = (term or "").strip()
    if not term:
        return []
    seen = set()
    results = []
    try:
        for p in search_patients(term, limit=limit):
            key = (p["name"], p["phone"])
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": p["name"], "phone": p["phone"]})
    except Exception:
        pass
    if len(results) < limit:
        try:
            for j in search_print_jobs(term, limit=30):
                name = (j.get("patient_name") or "").strip()
                if not name:
                    continue
                key = (name, j.get("customer_phone") or "")
                if key in seen:
                    continue
                seen.add(key)
                results.append({"name": name, "phone": j.get("customer_phone") or ""})
                if len(results) >= limit:
                    break
        except Exception:
            pass
    return results[:limit]


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
            "SELECT id, name, phone, allergy_note, hn_code FROM patients "
            "WHERE name LIKE ? OR phone LIKE ? "
            "ORDER BY CASE WHEN name LIKE ? OR phone LIKE ? THEN 0 ELSE 1 END, name LIMIT ?",
            (like, like, prefix_like, prefix_like, limit),
        ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "phone": r[2] or "",
                "allergy_note": r[3] or "", "hn_code": r[4] or "",
            }
            for r in rows
        ]
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


def add_patient_document(patient_id, title, body_text, image_bytes=None):
    """Resizes to at most 800px on the longest side before saving (storage
    space, not just display) - documents are stored as files on disk under
    PATIENT_DOCS_DIR, not as DB blobs, so the sqlite file itself stays small
    and the mobile page can serve them as plain static files.

    image_bytes is optional - this originally only supported a photo + short
    caption ("note"), but is also used to save a text note or an AI/patient
    chat transcript (title + body_text, no image needed) - see
    open_patient_profile_dialog()'s upload_doc() in label_gui.py. "note" is
    reused as the short title shown in the document list."""
    filename = None
    if image_bytes:
        patient_dir = os.path.join(PATIENT_DOCS_DIR, str(patient_id))
        os.makedirs(patient_dir, exist_ok=True)
        resized = _resize_image_bytes(image_bytes)
        filename = f"{uuid.uuid4().hex}.jpg"
        with open(os.path.join(patient_dir, filename), "wb") as f:
            f.write(resized)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO patient_documents (patient_id, image_path, note, uploaded_at, body_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (patient_id, filename, title or "", datetime.now().isoformat(), body_text or ""),
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
                "id": r[0], "title": r[2] or "", "uploaded_at": r[3],
                "has_image": bool(r[1]),
                "full_path": os.path.join(PATIENT_DOCS_DIR, str(patient_id), r[1]) if r[1] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_patient_document(doc_id):
    """Full record (title + body text + resolved image path if any) for the
    view popup."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT patient_id, image_path, note, uploaded_at, body_text FROM patient_documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        patient_id, image_path, note, uploaded_at, body_text = row
        return {
            "title": note or "", "body_text": body_text or "", "uploaded_at": uploaded_at,
            "has_image": bool(image_path),
            "full_path": os.path.join(PATIENT_DOCS_DIR, str(patient_id), image_path) if image_path else None,
        }
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


# ── Device warranties (ประกันอุปกรณ์) ──────────────────────────────────────


def _normalize_phone_digits(phone):
    return re.sub(r"[^0-9]", "", phone or "")


def format_phone_th(phone):
    """Thai phone display form 0xx-xxx-xxxx (10 digits). Handles +66 / 66."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    digits = _normalize_phone_digits(raw)
    if digits.startswith("66") and len(digits) >= 11:
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    if len(digits) == 9 and digits.startswith("0"):
        return f"{digits[0:2]}-{digits[2:5]}-{digits[5:9]}"
    return raw


def update_patient_contact(patient_id, name, phone):
    """Update name/phone; HN stays the same."""
    name = (name or "").strip()
    phone = format_phone_th(phone)
    if not name and not phone:
        raise ValueError("ต้องมีชื่อหรือเบอร์อย่างน้อยอย่างหนึ่ง")
    if not name:
        name = phone
    conn = _connect()
    try:
        conn.execute(
            "UPDATE patients SET name = ?, phone = ? WHERE id = ?",
            (name, phone, int(patient_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _normalize_person_name(name):
    n = re.sub(r"\s+", " ", (name or "").strip())
    for prefix in ("คุณ", "นาย", "นางสาว", "น.ส.", "นาง", "เด็กชาย", "เด็กหญิง", "ด.ช.", "ด.ญ."):
        if n.startswith(prefix):
            n = n[len(prefix):].strip()
            break
    return n.casefold()


def _names_compatible(a, b):
    """True when names look like the same person — exact or near-full substring.
    Rejects short-token traps like 'ทดสอบ' matching 'สุชาติ ทดสอบ 1'."""
    na = _normalize_person_name(a)
    nb = _normalize_person_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if shorter in longer and len(shorter) >= max(6, int(len(longer) * 0.75)):
        return True
    return False


def _parse_warranty_years(text):
    s = (text or "").strip()
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*ปี", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r"([\d.]+)\s*เดือน", s)
    if m:
        try:
            return float(m.group(1)) / 12.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date_flexible(text):
    s = (text or "").strip()
    if not s:
        return None
    for cand in (s, s[:10], s[:8]):
        if not cand:
            continue
        for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%y", "%d-%m-%Y"):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def _format_date_dmy(d):
    if d is None or d == "":
        return ""
    try:
        if hasattr(d, "strftime"):
            return d.strftime("%d/%m/%y")
        parsed = _parse_date_flexible(str(d))
        return parsed.strftime("%d/%m/%y") if parsed else str(d)[:10]
    except Exception:
        return str(d)[:10]


def _date_to_iso(d):
    if d is None:
        return None
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    parsed = _parse_date_flexible(str(d))
    return parsed.strftime("%Y-%m-%d") if parsed else None


def _add_years_to_date(purchase, years):
    if purchase is None or years is None:
        return None
    try:
        years = float(years)
    except (TypeError, ValueError):
        return None
    import calendar
    if not isinstance(purchase, date_cls):
        if hasattr(purchase, "date"):
            try:
                purchase = purchase.date()
            except Exception:
                return None
        else:
            return None
    whole = int(years)
    months_extra = int(round((years - whole) * 12))
    y = purchase.year + whole
    m = purchase.month + months_extra
    while m > 12:
        y += 1
        m -= 12
    while m < 1:
        y -= 1
        m += 12
    day = min(purchase.day, calendar.monthrange(y, m)[1])
    return date_cls(y, m, day)


def _list_patients_for_match():
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, name, IFNULL(phone, '') FROM patients").fetchall()
        return [{"id": r[0], "name": r[1], "phone": r[2] or ""} for r in rows]
    finally:
        conn.close()


def find_or_create_patient_for_warranty(name, phone):
    """Match flexible by phone digits / name; create patients (+ HN) if new.
    Returns (patient_id, how).

    If the typed name does not match anyone already on that phone, create a
    NEW patient — never silently reuse another customer's name.
    """
    name = (name or "").strip()
    phone = format_phone_th(phone)
    if not name and not phone:
        return None, "skip"
    exact = find_patient_id(name, phone)
    if exact:
        return exact, "exact"
    digits = _normalize_phone_digits(phone)
    nn = _normalize_person_name(name)
    patients = _list_patients_for_match()
    if digits and len(digits) >= 9:
        by_phone = [p for p in patients if _normalize_phone_digits(p["phone"]) == digits]
        if by_phone:
            if nn:
                for p in by_phone:
                    if _normalize_person_name(p["name"]) == nn:
                        return p["id"], "phone+name"
                for p in by_phone:
                    if _names_compatible(p["name"], name):
                        return p["id"], "phone+namefuzzy"
                pid = find_or_create_patient(name, phone)
                return pid, "created_phone_name_mismatch"
            if len(by_phone) == 1:
                return by_phone[0]["id"], "phone"
            return by_phone[0]["id"], "phone_ambiguous"
    if nn:
        by_name = [p for p in patients if _normalize_person_name(p["name"]) == nn]
        if len(by_name) == 1:
            return by_name[0]["id"], "name"
        if len(by_name) > 1 and digits:
            for p in by_name:
                if not _normalize_phone_digits(p["phone"]):
                    return p["id"], "name_emptyphone"
        fuzzy = [p for p in patients if _names_compatible(p["name"], name)]
        if len(fuzzy) == 1:
            return fuzzy[0]["id"], "namefuzzy"
    pid = find_or_create_patient(name or phone, phone)
    return pid, "created"


def list_warranty_product_names(term="", limit=40):
    """Product names seen in warranties — prefix first, then contain, newest first."""
    term = (term or "").strip()
    lim = max(1, min(int(limit), 100))
    conn = _connect()
    try:
        if term:
            like = f"%{term}%"
            prefix = f"{term}%"
            rows = conn.execute(
                """
                SELECT product_name FROM warranties
                WHERE product_name LIKE ?
                GROUP BY product_name
                ORDER BY
                  CASE WHEN product_name LIKE ? THEN 0 ELSE 1 END,
                  MAX(id) DESC
                LIMIT ?
                """,
                (like, prefix, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT product_name FROM warranties
                GROUP BY product_name
                ORDER BY MAX(id) DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [r[0] for r in rows if r and r[0]]
    finally:
        conn.close()


def get_latest_warranty_defaults(product_name):
    name = (product_name or "").strip()
    if not name:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT seller, seller_phone, price, warranty_years, note
            FROM warranties
            WHERE product_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if not row:
            return None
        return {
            "seller": row[0] or "",
            "seller_phone": row[1] or "",
            "price": row[2],
            "warranty_years": row[3],
            "note": row[4] or "",
        }
    finally:
        conn.close()


def list_warranties(limit=500, expiring_within_days=None):
    """All warranties, newest first (id DESC) — same default as HOPE desktop list.
    Optional filter: expiry within N days (includes overdue)."""
    lim = max(1, min(int(limit), 1000))
    conn = _connect()
    try:
        if expiring_within_days is not None:
            # SQLite date('now') is UTC-ish enough for day-level filter
            days = int(expiring_within_days)
            rows = conn.execute(
                f"""
                SELECT w.id, w.patient_id, w.product_name, w.seller, w.seller_phone,
                       w.price, w.purchase_date, w.warranty_years, w.expiry_date, w.note,
                       w.external_id, w.source, w.created_at,
                       IFNULL(p.name, '') AS patient_name,
                       IFNULL(p.phone, '') AS patient_phone,
                       IFNULL(p.hn_code, '') AS hn_code
                FROM warranties w
                LEFT JOIN patients p ON p.id = w.patient_id
                WHERE w.expiry_date IS NOT NULL AND w.expiry_date != ''
                  AND date(w.expiry_date) <= date('now', '+{days} days')
                ORDER BY date(w.expiry_date) ASC, w.id DESC
                LIMIT {lim}
                """
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT w.id, w.patient_id, w.product_name, w.seller, w.seller_phone,
                       w.price, w.purchase_date, w.warranty_years, w.expiry_date, w.note,
                       w.external_id, w.source, w.created_at,
                       IFNULL(p.name, '') AS patient_name,
                       IFNULL(p.phone, '') AS patient_phone,
                       IFNULL(p.hn_code, '') AS hn_code
                FROM warranties w
                LEFT JOIN patients p ON p.id = w.patient_id
                ORDER BY w.id DESC
                LIMIT {lim}
                """
            ).fetchall()
        cols = [
            "id", "patient_id", "product_name", "seller", "seller_phone",
            "price", "purchase_date", "warranty_years", "expiry_date", "note",
            "external_id", "source", "created_at",
            "patient_name", "patient_phone", "hn_code",
        ]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def list_warranties_for_patient(patient_id, limit=200):
    lim = max(1, min(int(limit), 500))
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT id, patient_id, product_name, seller, seller_phone,
                   price, purchase_date, warranty_years, expiry_date, note,
                   external_id, source, created_at
            FROM warranties
            WHERE patient_id = ?
            ORDER BY
              CASE WHEN expiry_date IS NULL OR expiry_date = '' THEN 1 ELSE 0 END,
              expiry_date ASC, id DESC
            LIMIT {lim}
            """,
            (int(patient_id),),
        ).fetchall()
        cols = [
            "id", "patient_id", "product_name", "seller", "seller_phone",
            "price", "purchase_date", "warranty_years", "expiry_date", "note",
            "external_id", "source", "created_at",
        ]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def add_warranty(
    patient_id,
    product_name,
    seller=None,
    seller_phone=None,
    price=None,
    purchase_date=None,
    warranty_years=None,
    expiry_date=None,
    note=None,
    external_id=None,
    source="manual",
):
    product_name = (product_name or "").strip()
    if not patient_id or not product_name:
        raise ValueError("ต้องมี patient_id และชื่อสินค้า")
    try:
        price_f = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price_f = None
    try:
        years_f = float(warranty_years) if warranty_years not in (None, "") else None
    except (TypeError, ValueError):
        years_f = _parse_warranty_years(str(warranty_years or ""))

    def _to_iso(val):
        if val is None or val == "":
            return None
        if isinstance(val, str):
            if re.match(r"^\d{4}-\d{2}-\d{2}", val):
                return val[:10]
            return _date_to_iso(_parse_date_flexible(val))
        return _date_to_iso(val)

    purchase_iso = _to_iso(purchase_date)
    expiry_iso = _to_iso(expiry_date)
    if expiry_iso is None and purchase_iso and years_f is not None:
        pur = _parse_date_flexible(purchase_iso)
        exp = _add_years_to_date(pur, years_f)
        expiry_iso = _date_to_iso(exp)
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO warranties (
                patient_id, product_name, seller, seller_phone, price,
                purchase_date, warranty_years, expiry_date, note, external_id, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(patient_id),
                product_name,
                (seller or "").strip() or None,
                format_phone_th(seller_phone) or None,
                price_f,
                purchase_iso,
                years_f,
                expiry_iso,
                (note or "").strip() or None,
                (external_id or "").strip() or None,
                (source or "manual").strip() or "manual",
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def warranty_days_left(expiry):
    """Days until expiry (negative = overdue), or None if unknown."""
    if expiry is None or expiry == "":
        return None
    d = expiry
    if isinstance(d, datetime):
        d = d.date()
    elif isinstance(d, str):
        d = _parse_date_flexible(d)
    if d is None or not hasattr(d, "toordinal"):
        return None
    return (d - date_cls.today()).days


def delete_warranty(warranty_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM warranties WHERE id = ?", (int(warranty_id),))
        conn.commit()
    finally:
        conn.close()


def update_warranty(warranty_id, **fields):
    allowed = {
        "product_name", "seller", "seller_phone", "price", "purchase_date",
        "warranty_years", "expiry_date", "note", "patient_id",
    }
    sets = []
    vals = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("purchase_date", "expiry_date"):
            if isinstance(v, str) and v:
                if re.match(r"^\d{4}-\d{2}-\d{2}", v):
                    v = v[:10]
                else:
                    v = _date_to_iso(_parse_date_flexible(v))
            elif v is not None:
                v = _date_to_iso(v)
        elif k in ("price", "warranty_years"):
            try:
                v = float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                v = None
        elif k == "patient_id" and v is not None:
            v = int(v)
        elif k == "seller_phone" and v is not None:
            v = format_phone_th(v) or None
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(int(warranty_id))
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE warranties SET {', '.join(sets)} WHERE id = ?",
            tuple(vals),
        )
        conn.commit()
    finally:
        conn.close()


def warranty_exists_external_id(external_id):
    if not external_id:
        return False
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM warranties WHERE external_id = ? LIMIT 1",
            (external_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _warranty_external_id_from_row(product, customer, phone, purchase, price, expiry):
    raw = "|".join([
        (product or "").strip(),
        (customer or "").strip(),
        _normalize_phone_digits(phone),
        (purchase or "").strip()[:10],
        (price or "").strip(),
        (expiry or "").strip()[:10],
    ])
    return ("csv:" + raw)[:120]


def import_warranties_from_csv(csv_path):
    """Import warranty_tracker export CSV into patients + warranties.
    Idempotent on external_id. Returns summary dict."""
    import csv as csv_mod

    report = {
        "path": csv_path,
        "rows": 0,
        "inserted": 0,
        "skipped_dup": 0,
        "skipped_bad": 0,
        "patients_created": 0,
        "patients_matched": 0,
        "errors": [],
        "details": [],
    }
    if not os.path.isfile(csv_path):
        report["errors"].append(f"ไม่พบไฟล์: {csv_path}")
        return report

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv_mod.DictReader(f)
        raw_rows = list(reader)
    report["rows"] = len(raw_rows)

    def phone_key(r):
        return 0 if _normalize_phone_digits(r.get("เบอร์โทร") or r.get("phone") or "") else 1

    raw_rows.sort(key=phone_key)

    for r in raw_rows:
        product = (r.get("ชื่อสินค้า") or r.get("name") or "").strip()
        customer = (r.get("ชื่อลูกค้า") or r.get("customer") or "").strip()
        phone = (r.get("เบอร์โทร") or r.get("phone") or "").strip()
        seller = (r.get("ซื้อสินค้ามาจาก") or r.get("seller") or "").strip()
        seller_phone = (r.get("เบอร์โทรร้านค้า") or r.get("sellerPhone") or "").strip()
        price_s = (r.get("ราคา") or r.get("price") or "").strip()
        purchase_s = (r.get("วันที่ซื้อ") or r.get("purchase") or "").strip()
        years_s = (r.get("ระยะประกัน") or r.get("years") or "").strip()
        expiry_s = (r.get("วันหมดประกัน") or r.get("expiry") or "").strip()
        note = (r.get("หมายเหตุ") or r.get("note") or "").strip()
        note = note.replace("\r\n", "\n").replace("\r", "\n")

        if not product or not customer:
            report["skipped_bad"] += 1
            report["details"].append({"product": product, "customer": customer, "status": "bad_row"})
            continue

        ext = _warranty_external_id_from_row(product, customer, phone, purchase_s, price_s, expiry_s)
        if warranty_exists_external_id(ext):
            report["skipped_dup"] += 1
            report["details"].append({"product": product, "customer": customer, "status": "dup", "external_id": ext})
            continue

        try:
            pid, how = find_or_create_patient_for_warranty(customer, phone)
            if not pid:
                report["skipped_bad"] += 1
                continue
            if how in ("created", "created_phone_name_mismatch"):
                report["patients_created"] += 1
            else:
                report["patients_matched"] += 1

            price = None
            if price_s:
                try:
                    price = float(price_s.replace(",", ""))
                except ValueError:
                    price = None
            years = _parse_warranty_years(years_s)
            purchase_date = _parse_date_flexible(purchase_s)
            expiry_date = _parse_date_flexible(expiry_s)

            wid = add_warranty(
                patient_id=pid,
                product_name=product,
                seller=seller,
                seller_phone=seller_phone,
                price=price,
                purchase_date=purchase_date,
                warranty_years=years,
                expiry_date=expiry_date,
                note=note,
                external_id=ext,
                source="import_csv",
            )
            report["inserted"] += 1
            report["details"].append({
                "product": product,
                "customer": customer,
                "status": "inserted",
                "patient_id": pid,
                "how": how,
                "warranty_id": wid,
            })
        except Exception as e:
            report["errors"].append(f"{customer} / {product}: {e}")
            report["details"].append({
                "product": product, "customer": customer, "status": "error", "error": str(e),
            })

    return report


def count_warranties():
    conn = _connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM warranties").fetchone()[0])
    finally:
        conn.close()


def clear_all_warranties(also_orphan_patients=False):
    """Delete every warranty row. If also_orphan_patients, also delete patients
    who have no print_jobs and no remaining warranties (import/test cleanup
    without wiping real label-print customers).

    Returns dict: warranties_deleted, patients_deleted.
    """
    conn = _connect()
    try:
        n_w = conn.execute("SELECT COUNT(*) FROM warranties").fetchone()[0]
        conn.execute("DELETE FROM warranties")
        n_p = 0
        if also_orphan_patients:
            # patients with no warranties (all gone) and no print history link
            orphan_ids = [
                r[0] for r in conn.execute(
                    """
                    SELECT p.id FROM patients p
                    WHERE NOT EXISTS (SELECT 1 FROM warranties w WHERE w.patient_id = p.id)
                      AND NOT EXISTS (SELECT 1 FROM print_jobs j WHERE j.patient_id = p.id)
                      AND NOT EXISTS (
                        SELECT 1 FROM print_jobs j2
                        WHERE j2.patient_name = p.name
                          AND IFNULL(j2.customer_phone, '') = IFNULL(p.phone, '')
                      )
                    """
                ).fetchall()
            ]
            for pid in orphan_ids:
                # remove docs on disk
                patient_dir = os.path.join(PATIENT_DOCS_DIR, str(pid))
                if os.path.isdir(patient_dir):
                    shutil.rmtree(patient_dir, ignore_errors=True)
                conn.execute("DELETE FROM patient_documents WHERE patient_id = ?", (pid,))
                conn.execute("DELETE FROM patients WHERE id = ?", (pid,))
                n_p += 1
        conn.commit()
        return {"warranties_deleted": int(n_w), "patients_deleted": int(n_p)}
    finally:
        conn.close()


def clear_imported_and_test_warranties(also_orphan_patients=True):
    """Remove warranties from import_csv / test sources only (keep real mobile/manual).
    Useful after loading CSV for a dry-run before shipping to a customer shop."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, patient_id FROM warranties WHERE source IN ('import_csv', 'test')"
        ).fetchall()
        n_w = len(rows)
        patient_ids = {r[1] for r in rows if r[1]}
        conn.execute("DELETE FROM warranties WHERE source IN ('import_csv', 'test')")
        n_p = 0
        if also_orphan_patients and patient_ids:
            for pid in patient_ids:
                still = conn.execute(
                    "SELECT 1 FROM warranties WHERE patient_id = ? LIMIT 1", (pid,)
                ).fetchone()
                if still:
                    continue
                job = conn.execute(
                    "SELECT 1 FROM print_jobs WHERE patient_id = ? LIMIT 1", (pid,)
                ).fetchone()
                if job:
                    continue
                patient_dir = os.path.join(PATIENT_DOCS_DIR, str(pid))
                if os.path.isdir(patient_dir):
                    shutil.rmtree(patient_dir, ignore_errors=True)
                conn.execute("DELETE FROM patient_documents WHERE patient_id = ?", (pid,))
                conn.execute("DELETE FROM patients WHERE id = ?", (pid,))
                n_p += 1
        conn.commit()
        return {"warranties_deleted": n_w, "patients_deleted": n_p}
    finally:
        conn.close()






# ── ข.ย.9 / ข.ย.11 ───────────────────────────────────────────────────────────
# Ported from HOPE label_printer. Behaviour is deliberately identical; the only
# structural difference is that a drug is identified by drug_templates.id
# (template_id) rather than a POS catalog idproduct, because this build has no
# POS to key against.


def set_drug_report_category(template_id, category):
    """'none' | 'dangerous' | 'tramadol' - which ledger, if any, a dispense of
    this drug has to be reported in."""
    if category not in ("none", "dangerous", "tramadol"):
        raise ValueError("category ไม่ถูกต้อง")
    conn = _connect()
    try:
        conn.execute(
            "UPDATE drug_templates SET drug_report_category = ? WHERE id = ?",
            (category, int(template_id)),
        )
        conn.commit()
    finally:
        conn.close()


def get_drug_report_category(template_id):
    if not template_id:
        return "none"
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT drug_report_category FROM drug_templates WHERE id = ?", (int(template_id),)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else "none"


# ── ข.ย.9: purchase lots ─────────────────────────────────────────────────────


def save_purchase_lot(template_id, drug_name, received_date, source_company, lot_number,
                      qty, unit_name, exp_date=""):
    """Record a lot received. qty_remaining starts at qty_received and is drawn
    down by fifo_decrement_lot() as the drug is dispensed."""
    qty = float(qty)
    if qty <= 0:
        raise ValueError("จำนวนที่รับต้องมากกว่า 0")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO purchase_lots (template_id, drug_name, received_date, source_company, "
            " lot_number, exp_date, unit_name, qty_received, qty_remaining, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(template_id) if template_id else None, drug_name, received_date,
             source_company or "", lot_number or "", exp_date or "", unit_name or "",
             qty, qty, datetime.now().isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_purchase_lots(template_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, drug_name, received_date, source_company, lot_number, exp_date, "
            "       unit_name, qty_received, qty_remaining "
            "FROM purchase_lots WHERE template_id = ? ORDER BY received_date ASC, id ASC",
            (int(template_id),),
        ).fetchall()
    finally:
        conn.close()
    return [{
        "id": r[0], "drug_name": r[1], "received_date": r[2], "source_company": r[3] or "",
        "lot_number": r[4] or "", "exp_date": r[5] or "", "unit_name": r[6] or "",
        "qty_received": float(r[7] or 0), "qty_remaining": float(r[8] or 0),
    } for r in rows]


def delete_purchase_lot(lot_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM purchase_lots WHERE id = ?", (int(lot_id),))
        conn.commit()
    finally:
        conn.close()


def fifo_decrement_lot(template_id, qty):
    """Draw qty from the oldest lot of this drug that still has stock, clamped
    at 0. Returns (lot_id, lot_number), or (None, None) when no lot is on file -
    a print is never blocked over ledger bookkeeping."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, lot_number, qty_remaining FROM purchase_lots "
            "WHERE template_id = ? AND qty_remaining > 0 ORDER BY received_date ASC, id ASC LIMIT 1",
            (int(template_id),),
        ).fetchone()
        if not row:
            return None, None
        lot_id, lot_number, remaining = row[0], row[1], float(row[2] or 0)
        conn.execute(
            "UPDATE purchase_lots SET qty_remaining = ? WHERE id = ?",
            (max(0.0, remaining - float(qty)), lot_id),
        )
        conn.commit()
        return lot_id, lot_number
    finally:
        conn.close()


def get_lot_remaining(lot_id):
    if not lot_id:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT qty_remaining, qty_received, unit_name FROM purchase_lots WHERE id = ?",
            (int(lot_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"remaining": float(row[0] or 0), "received": float(row[1] or 0), "unit": row[2] or ""}


def build_ky9_rows(date_from=None, date_to=None):
    """One row per lot received in range, ordered drug then date so
    build_ky9_sheets() can group straight off it."""
    where, params = [], []
    if date_from:
        where.append("date(received_date) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(received_date) <= date(?)")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT received_date, source_company, drug_name, lot_number, qty_received, "
            "       unit_name, exp_date, template_id "
            "FROM purchase_lots" + where_sql + " ORDER BY drug_name, received_date ASC, id ASC",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [{
        "seq": i + 1, "date": str(r[0])[:10], "seller": r[1] or "", "drug_name": r[2],
        "lot_number": r[3] or "", "qty": r[4], "unit_name": r[5] or "",
        "exp_date": r[6] or "", "template_id": r[7],
    } for i, r in enumerate(rows)]


def build_ky9_sheets(date_from=None, date_to=None):
    """One sheet per drug - ข.ย.9 is filed per drug, so a single flat table
    covering every drug isn't a usable ledger page. Sequence restarts at 1 in
    each sheet. Grouped by template_id, falling back to the name."""
    sheets, order = {}, []
    for r in build_ky9_rows(date_from, date_to):
        key = r.get("template_id") or "name:" + r["drug_name"]
        if key not in sheets:
            sheets[key] = {"product_name": r["drug_name"], "unit_name": r["unit_name"] or "",
                           "sources": [], "rows": []}
            order.append(key)
        sh = sheets[key]
        row = dict(r)
        row["seq"] = len(sh["rows"]) + 1
        sh["rows"].append(row)
        if r["seller"] and r["seller"] not in sh["sources"]:
            sh["sources"].append(r["seller"])
    return [sheets[k] for k in order]


# ── ข.ย.11: controlled sales ─────────────────────────────────────────────────


def save_controlled_sale(template_id, drug_name, lot_id, lot_number, qty, unit_name, category,
                         buyer_name, buyer_citizen_id="", buyer_address="",
                         print_job_id=None, patient_id=None):
    """One ข.ย.11 sale row. For tramadol the citizen ID/address are blank unless
    the caller pre-filled them, leaving info_complete=0 to be finished later
    from the report screen."""
    complete = 1 if (category != "tramadol" or (buyer_citizen_id and buyer_address)) else 0
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO controlled_sales (template_id, drug_name, lot_id, lot_number, unit_name, "
            " qty, category, buyer_name, buyer_citizen_id, buyer_address, info_complete, sold_at, "
            " print_job_id, patient_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(template_id) if template_id else None, drug_name, lot_id, lot_number or "",
             unit_name or "", float(qty), category, buyer_name or "", buyer_citizen_id or "",
             buyer_address or "", complete, datetime.now().isoformat(), print_job_id,
             int(patient_id) if patient_id else None),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fill_controlled_sale_info(sale_id, citizen_id, address):
    """Deferred tramadol entry - fill in citizen ID + address after the fact."""
    complete = 1 if (citizen_id and address) else 0
    conn = _connect()
    try:
        conn.execute(
            "UPDATE controlled_sales SET buyer_citizen_id = ?, buyer_address = ?, info_complete = ? "
            "WHERE id = ?",
            (citizen_id or "", address or "", complete, int(sale_id)),
        )
        conn.commit()
    finally:
        conn.close()


def update_controlled_sale_basics(sale_id, date_iso, buyer_name):
    """Correct the sale date and buyer name. The date keeps its original
    time-of-day so same-day ordering within a sheet stays stable."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT sold_at FROM controlled_sales WHERE id = ?", (int(sale_id),)
        ).fetchone()
        if not row:
            raise ValueError("ไม่พบรายการขายนี้")
        old = str(row[0] or "")
        time_part = old[10:] if len(old) > 10 else ""
        conn.execute(
            "UPDATE controlled_sales SET sold_at = ?, buyer_name = ? WHERE id = ?",
            (str(date_iso) + time_part, buyer_name or "", int(sale_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _clamp_lot(conn, lot_id, delta):
    """Move a lot's qty_remaining by a signed delta, kept inside
    0..qty_received - a correction must never drive a lot negative or above
    what was received."""
    row = conn.execute(
        "SELECT qty_remaining, qty_received FROM purchase_lots WHERE id = ?", (int(lot_id),)
    ).fetchone()
    if not row:
        return
    remaining, received = float(row[0] or 0), float(row[1] or 0)
    conn.execute(
        "UPDATE purchase_lots SET qty_remaining = ? WHERE id = ?",
        (min(received, max(0.0, remaining + delta)), int(lot_id)),
    )


def adjust_controlled_sale_qty(sale_id, new_qty):
    """Correct a recorded quantity, moving the difference back into (or out of)
    the lot it came from - staff mis-key quantities, and fixing the ledger
    without fixing the stock just trades one wrong number for another."""
    new_qty = float(new_qty)
    if new_qty <= 0:
        raise ValueError("จำนวนต้องมากกว่า 0")
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT qty, lot_id FROM controlled_sales WHERE id = ?", (int(sale_id),)
        ).fetchone()
        if not row:
            raise ValueError("ไม่พบรายการขายนี้")
        old_qty, lot_id = float(row[0] or 0), row[1]
        conn.execute("UPDATE controlled_sales SET qty = ? WHERE id = ?", (new_qty, int(sale_id)))
        if lot_id:
            _clamp_lot(conn, lot_id, old_qty - new_qty)
        conn.commit()
    finally:
        conn.close()


def delete_controlled_sale(sale_id):
    """Remove one row and put its quantity back into its lot."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT qty, lot_id FROM controlled_sales WHERE id = ?", (int(sale_id),)
        ).fetchone()
        if not row:
            return
        qty, lot_id = float(row[0] or 0), row[1]
        if lot_id and qty:
            _clamp_lot(conn, lot_id, qty)
        conn.execute("DELETE FROM controlled_sales WHERE id = ?", (int(sale_id),))
        conn.commit()
    finally:
        conn.close()


def clear_controlled_sales(category=None):
    """Wipe ข.ย.11 sale rows - one category, or all - returning every quantity
    to its lot. purchase_lots rows (the ข.ย.9 purchase ledger) are never
    deleted. Returns how many sale rows were removed."""
    conn = _connect()
    try:
        if category:
            rows = conn.execute(
                "SELECT lot_id, SUM(qty) FROM controlled_sales "
                "WHERE lot_id IS NOT NULL AND category = ? GROUP BY lot_id", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT lot_id, SUM(qty) FROM controlled_sales "
                "WHERE lot_id IS NOT NULL GROUP BY lot_id"
            ).fetchall()
        for lot_id, total in rows:
            _clamp_lot(conn, lot_id, float(total or 0))
        if category:
            cur = conn.execute("DELETE FROM controlled_sales WHERE category = ?", (category,))
        else:
            cur = conn.execute("DELETE FROM controlled_sales")
        removed = cur.rowcount
        conn.commit()
        return removed
    finally:
        conn.close()


def get_last_tramadol_buyer_info(patient_id, buyer_name=None):
    """Citizen ID + address from this buyer's most recent completed tramadol
    sale. Tries patient_id first, then an exact buyer_name - a sale can
    legitimately have no patient link, and without the fallback that history is
    invisible even though it's on file. "matched_by" tells the dialog which
    basis was used so it can ask for a closer look on the weaker one."""
    def q(where, param):
        conn = _connect()
        try:
            return conn.execute(
                "SELECT buyer_citizen_id, buyer_address FROM controlled_sales "
                "WHERE " + where + " AND category = 'tramadol' AND info_complete = 1 "
                "  AND IFNULL(buyer_citizen_id, '') != '' AND IFNULL(buyer_address, '') != '' "
                "ORDER BY sold_at DESC, id DESC LIMIT 1",
                (param,),
            ).fetchone()
        finally:
            conn.close()

    row, matched_by = None, None
    if patient_id:
        row, matched_by = q("patient_id = ?", int(patient_id)), "patient"
    if not row and (buyer_name or "").strip():
        row, matched_by = q("buyer_name = ?", buyer_name.strip()), "name"
    if not row:
        return None
    return {"citizen_id": row[0] or "", "address": row[1] or "", "matched_by": matched_by}


def build_ky11_sheets(category, date_from=None, date_to=None):
    """One sheet per drug, each with its lot summary and its sales, mirroring
    HOPE's build_ky11_sheets(). Sales carry lot_remaining so the report screen
    can show what's left in the lot a sale came out of."""
    where, params = ["category = ?"], [category]
    if date_from:
        where.append("date(sold_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(sold_at) <= date(?)")
        params.append(date_to)
    conn = _connect()
    try:
        sale_rows = conn.execute(
            "SELECT id, template_id, drug_name, lot_number, unit_name, qty, buyer_name, "
            "       buyer_citizen_id, buyer_address, info_complete, sold_at, lot_id "
            "FROM controlled_sales WHERE " + " AND ".join(where) +
            " ORDER BY drug_name, sold_at ASC, id ASC",
            params,
        ).fetchall()
        template_ids = sorted({r[1] for r in sale_rows if r[1] is not None})
        lots_by_template, remaining_by_lot = {}, {}
        if template_ids:
            marks = ",".join("?" for _ in template_ids)
            for r in conn.execute(
                "SELECT template_id, lot_number, received_date, qty_received, source_company, "
                "       id, qty_remaining FROM purchase_lots WHERE template_id IN (" + marks + ") "
                "ORDER BY received_date ASC, id ASC", template_ids,
            ).fetchall():
                lots_by_template.setdefault(r[0], []).append({
                    "lot_number": r[1] or "", "received_date": str(r[2])[:10],
                    "qty_received": float(r[3] or 0), "source": r[4] or "",
                })
                remaining_by_lot[r[5]] = float(r[6] or 0)
    finally:
        conn.close()

    sheets, order = {}, []
    for (sale_id, template_id, drug_name, lot_number, unit_name, qty, buyer_name,
         citizen_id, address, info_complete, sold_at, lot_id) in sale_rows:
        key = template_id if template_id is not None else "name:" + drug_name
        if key not in sheets:
            lots = lots_by_template.get(template_id, [])
            sheets[key] = {
                "product_id": template_id, "product_name": drug_name, "unit_name": unit_name or "",
                "lots": lots,
                "sources": sorted({lt["source"] for lt in lots if lt["source"]}),
                "sales": [],
            }
            order.append(key)
        buyer_block = buyer_name or ""
        if category == "tramadol":
            buyer_block = "\n".join([
                buyer_name or "-",
                citizen_id or "(ยังไม่กรอกเลขบัตร)",
                address or "(ยังไม่กรอกที่อยู่)",
            ])
        sheets[key]["sales"].append({
            "id": sale_id, "seq": len(sheets[key]["sales"]) + 1, "date": str(sold_at)[:10],
            "qty": qty, "lot_number": lot_number or "-", "buyer_name": buyer_name or "",
            "buyer_block": buyer_block, "citizen_id": citizen_id or "", "address": address or "",
            "info_complete": bool(info_complete), "lot_id": lot_id,
            "lot_remaining": remaining_by_lot.get(lot_id) if lot_id else None,
        })
    return [sheets[k] for k in order]


# ── ใบส่งต่อผู้ป่วย (PhRF) ────────────────────────────────────────────────────

REFERRAL_FIELDS = [
    "patient_id", "patient_name", "gender", "age", "citizen_id", "address", "phone",
    "right_type", "right_other", "hospital",
    "reason_further", "reason_drp", "reason_followup",
    "med_review", "chief_complaint", "illness_history", "chronic_disease",
    "allergy_history", "extra_info", "problem_found",
    "act_treat", "act_treat_text", "act_advice", "act_advice_text",
    "act_other", "act_other_text",
    "pharmacist_name", "license_no", "referred_at",
]

_REFERRAL_FLAGS = {"reason_further", "reason_drp", "reason_followup",
                   "act_treat", "act_advice", "act_other"}


def save_referral(data):
    """Insert one referral, returns its id."""
    vals = []
    for f in REFERRAL_FIELDS:
        v = data.get(f)
        if f == "patient_id":
            vals.append(int(v) if v else None)
        elif f in _REFERRAL_FLAGS:
            vals.append(1 if v else 0)
        else:
            vals.append(v if v is not None else "")
    cols = ", ".join(REFERRAL_FIELDS) + ", created_at"
    marks = ", ".join("?" for _ in REFERRAL_FIELDS) + ", ?"
    vals.append(datetime.now().isoformat(timespec="seconds"))
    conn = _connect()
    try:
        cur = conn.execute(f"INSERT INTO referrals ({cols}) VALUES ({marks})", vals)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_referrals(term="", limit=200):
    """Most recent first, optionally filtered by patient name."""
    conn = _connect()
    try:
        where, params = "", []
        if (term or "").strip():
            where = "WHERE patient_name LIKE ?"
            params.append(f"%{term.strip()}%")
        rows = conn.execute(
            "SELECT id, patient_name, IFNULL(hospital, ''), referred_at, "
            "       IFNULL(chief_complaint, '') "
            f"FROM referrals {where} ORDER BY referred_at DESC, id DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [{"id": r[0], "patient_name": r[1], "hospital": r[2],
                 "referred_at": str(r[3])[:16], "chief_complaint": r[4]} for r in rows]
    finally:
        conn.close()


def get_referral(referral_id):
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT {', '.join(REFERRAL_FIELDS)} FROM referrals WHERE id = ?",
            (int(referral_id),),
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(REFERRAL_FIELDS, row)) if row else None


def delete_referral(referral_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM referrals WHERE id = ?", (int(referral_id),))
        conn.commit()
    finally:
        conn.close()
