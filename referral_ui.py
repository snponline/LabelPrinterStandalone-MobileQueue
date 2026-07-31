"""ใบส่งต่อผู้ป่วย (Pharmacist Referral Form) for LabelPrinterStandalone_MobileQueue.

Ported from HOPE label_printer - same two-part สปสช./สภช. form, same three
printed sheets (ฉบับร้านยา / ฉบับผู้ป่วย / ใบตอบกลับ), same symptom picker.
What differs is underneath: referrals live in local SQLite via storage.py
instead of SQL Server, and the shop block comes from app_settings rather than
label_gui.COMPANY_INFO.
"""
from __future__ import annotations

import html
import os
import tempfile
import uuid
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import messagebox, ttk

import app_settings
import storage

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

RIGHT_OPTIONS = ["UC", "ข้าราชการ", "ปกส", "อื่นๆ"]

REASON_OPTIONS = [
    ("further", "Further investigation/management"),
    ("drp", "Drug-related problems"),
    ("followup", "Loss of follow-up"),
]

# อาการสำคัญ .. ข้อมูลเพิ่มเติม are usually written by hand at the counter, so
# each gets this many ruled lines on the printed sheet whether or not anything
# was typed into it.
BLANK_LINES = 5

DEFAULT_RIGHT_TYPE = "อื่นๆ"

# Red-flag symptoms that come up often enough to be worth one click instead of
# retyping. Groups with an empty list still appear in the dropdown so the set
# is visibly complete; their items get filled in later.
SYMPTOM_GROUPS = [
    ("1. ระบบทางเดินหายใจ", [
        "ปวดบริเวณหน้าอกและหายใจเข้า",
        "หายใจสั่นและเร็ว",
        "เสมหะมีเลือดปน",
        "ไอติดต่อกันเป็นเวลานาน 3-4 สัปดาห์",
        "ไอเป็นเสียงไอกรน",
    ]),
    ("2. ระบบหัวใจ และหลอดเลือด", [
        "ปวดเค้นหน้าอก",
        "หัวใจเต้นเร็วหรือจังหวะการเต้นผิดปกติ",
        "นอนราบไม่ได้",
        "เหนื่อยง่ายผิดปกติ",
        "หน้ามืด เป็นลมหลายครั้งโดยไม่มีสาเหตุ",
        "มีอาการชาครึ่งซีกของร่างกาย",
    ]),
    ("3. ระบบทางเดินอาหาร", [
        "กลืนลำบาก",
        "อาเจียนมีเลือดปน",
        "อุจจาระมีเลือดปนในปริมาณมากหรือเป็นแบบเรื้อรัง",
        "อาเจียนรุนแรงร่วมกับถ่ายอุจจาระไม่ออกมาหลายวัน",
        "การคลำ พบกล้ามเนื้อในช่องท้อง",
        "ตัวเหลืองตาเหลือง",
        "น้ำหนักลดมากผิดปกติโดยไม่ทราบสาเหตุ",
    ]),
    ("4. อาการผิดปกติเกี่ยวกับหู", [
        "ปวดหูมาก",
        "มีของเหลวไหลออกจากหู",
        "หูหนวกไม่ได้ยินเสียง หรือได้ยินเสียงน้อยลงจากปกติ",
        "วิงเวียนศีรษะไม่ทราบสาเหตุ เดินเซ หรือไม่สามารถทรงตัวได้",
    ]),
    ("5. อาการผิดปกติเกี่ยวกับตา", [
        "ตาแดงร่วมกับปวดตามาก ภาวะตาสู้แสงไม่ได้",
        "ตามัว หรือมองเห็นผิดปกติ เช่น มองเห็นภาพซ้อน มองเห็นวงแสงรอบวัตถุ",
    ]),
    ("6. อาการผิดปกติเกี่ยวกับระบบสืบพันธุ์ และทางเดินปัสสาวะ", [
        "ปัสสาวะไม่ออก",
        "ปัสสาวะมีเลือดปน",
        "ปัสสาวะแสบขัด ร่วมกับอาการปวดท้อง/สะโพก/หลัง",
        "ปัสสาวะแสบขัดร่วมกับมีไข้",
        "เลือดออกจากช่องคลอดในหญิงขณะตั้งครรภ์",
        "มีระดูผิดปกติ (ประจำเดือน)",
    ]),
    ("7. อาการผิดปกติทางระบบประสาท", [
        "ปวดศีรษะรุนแรง",
        "อาการชา หรืออ่อนแรงที่เกิดขึ้นฉับพลัน หรือเป็นข้างใดข้างหนึ่งของร่างกาย",
        "การมองเห็นภาพผิดปกติที่เกิดขึ้นอย่างฉับพลัน",
        "การพูดไม่ชัด หน้าเบี้ยว ปากเบี้ยว พูดไม่ได้ ไม่ตอบสนองต่อคำพูด",
        "อาการชัก",
    ]),
    ("8. อาการผิดปกติอื่นๆ", [
        "โรคผิวหนังที่มีอาการรุนแรง เช่น สะเก็ดเงินหรือเรื้อนกวาง",
        "คอแข็งร่วมกับมีไข้",
        "อาเจียนเรื้อรัง",
        "ซึม",
        "ไม่ค่อยรู้สึกตัว การรับรู้เกี่ยวกับเวลา สถานที่ บุคคล ลดลง",
        "หมดสติอย่างเฉียบพลัน ตัวเย็น",
        "อาการติดเชื้ออื่นๆ เช่น ไข้หวัดใหญ่ ไข้เลือดออก มาลาเรีย อาการแพ้ยา covid",
    ]),
]



def fs(n):
    """Match label_gui.fs for dialog sizing."""
    try:
        import label_gui as _lg
        if hasattr(_lg, "fs"):
            return _lg.fs(n)
    except Exception:
        pass
    return max(1, int(round(n)))


def shop_defaults():
    """The shop block already printed on every drug label - reused here so the
    referral footer fills itself in without anyone retyping the shop's own
    details."""
    s = app_settings.load_settings() or {}
    address = " ".join(x for x in (s.get("address_line1"), s.get("address_line2")) if x)
    return {"shop_name": s.get("company_name") or "",
            "shop_phone": s.get("phone") or "",
            "shop_address": address}


def pharmacist_choices():
    """settings["pharmacist_names"] is the "//"-separated list printed on the
    label ("สนั่น//จิรพัส"); a referral is signed by one of them."""
    raw = (app_settings.load_settings() or {}).get("pharmacist_names") or ""
    return [n.strip() for n in raw.split("//") if n.strip()]


# ── storage ────────────────────────────────────────────────────────

# storage._connect() creates the table, so opening the DB at all is enough.
save_referral = storage.save_referral
list_referrals = storage.list_referrals
get_referral = storage.get_referral
delete_referral = storage.delete_referral


def ensure_referrals_table():
    storage._connect().close()

# ── printed form ─────────────────────────────────────────────────────────────


def _esc(s):
    return html.escape(str(s or ""))


def _val(s):
    """A filled-in value on a dotted line; keeps the line visible when blank."""
    s = (s or "").strip()
    return _esc(s) if s else "&nbsp;"


def _multiline(s, lines=3):
    """Typed content on one underlined block; an empty field becomes `lines`
    ruled lines to write on, since most of this form is filled in by hand at
    the counter."""
    s = (s or "").strip()
    if s:
        return f'<div class="ml">{_esc(s).replace(chr(10), "<br>")}</div>'
    return "".join('<div class="rule"></div>' for _ in range(max(1, int(lines))))


def _cid_boxes(cid):
    """13 boxes, 1-4-5-2-1 like the official form."""
    digits = [c for c in (cid or "") if c.isdigit()]
    groups, out, idx = [1, 4, 5, 2, 1], [], 0
    for gi, g in enumerate(groups):
        cells = ""
        for _ in range(g):
            cells += f'<span class="cidbox">{_esc(digits[idx]) if idx < len(digits) else "&nbsp;"}</span>'
            idx += 1
        out.append(cells)
    return '<span class="ciddash">-</span>'.join(out)


def _chk(on):
    return "☑" if on else "☐"


CSS = """
  @page { size: A4; margin: 1.0cm 1.1cm; }
  body { font-family: Tahoma, sans-serif; color:#111; margin:0; font-size:12px; }
  .sheet { page-break-after: always; padding:4px 0; }
  .sheet:last-child { page-break-after: auto; }
  .hdr { display:flex; align-items:center; gap:10px; margin-bottom:6px; }
  .hdr .org { font-size:11px; font-weight:700; line-height:1.3; }
  .cid { margin-left:auto; font-size:12px; white-space:nowrap; }
  .cidbox { display:inline-block; width:15px; height:19px; border:1px solid #333;
            text-align:center; line-height:19px; margin:0 1px; font-size:11px; }
  .ciddash { padding:0 2px; }
  .title { background:#f6c9a4; text-align:center; font-weight:700; padding:4px;
           border:1px solid #e0a97a; margin:6px 0 8px; }
  .row { margin:3px 0; line-height:1.9; }
  .f { border-bottom:1px dotted #666; display:inline-block; min-width:60px;
       padding:0 4px; }
  .cols { display:flex; gap:10px; margin-top:8px; }
  .col { flex:1; border:1px solid #ccc; }
  .colhead { background:#f6c9a4; text-align:center; font-weight:700; padding:3px;
             border-bottom:1px solid #e0a97a; }
  .colbody { padding:6px 8px; min-height:330px; }
  .lbl { font-weight:700; margin-top:5px; }
  .ml { border-bottom:1px dotted #666; white-space:pre-line; min-height:1.5em; }
  .rule { border-bottom:1px dotted #666; height:1.5em; }
  .band { background:#f6c9a4; text-align:center; font-weight:700; padding:4px;
          border:1px solid #e0a97a; margin:10px 0 8px; }
  .sign { margin-top:16px; text-align:center; line-height:2.1; }
  .shop { border-top:1px solid #333; margin-top:12px; padding-top:6px; line-height:2.0; }
  .copytag { float:right; font-size:11px; font-weight:700; border:1px solid #333;
             padding:1px 8px; }
  @media print { .no-print { display:none !important; } }
"""


def _phrf_sheet(d, copy_label):
    reasons = "".join(
        f'<span style="margin-right:14px;">{_chk(d.get("reason_" + key))} {label}</span>'
        for key, label in REASON_OPTIONS)
    rights = "".join(
        f'<span style="margin-right:10px;">{_chk(d.get("right_type") == r)} {r}</span>'
        for r in RIGHT_OPTIONS)
    return f"""
<div class="sheet">
  <div class="hdr">
    <div class="org">สภช CPA<br><span style="font-weight:400;font-size:10px;">สมาคมเภสัชกรรมชุมชน</span></div>
    <div class="org">สปสช.<br><span style="font-weight:400;font-size:10px;">สำนักงานหลักประกันสุขภาพแห่งชาติ</span></div>
    <div class="cid">รหัสประจำตัวประชาชน {_cid_boxes(d.get("citizen_id"))}</div>
  </div>
  <div class="title">แบบฟอร์มการประเมินและส่งต่อผู้ป่วย (Pharmacist Referral Form: PhRF)
    <span class="copytag">{_esc(copy_label)}</span></div>

  <div class="row">ชื่อผู้ป่วย <span class="f" style="min-width:330px;">{_val(d.get("patient_name"))}</span>
    เพศ <span class="f" style="min-width:70px;">{_val(d.get("gender"))}</span>
    อายุ <span class="f" style="min-width:45px;">{_val(d.get("age"))}</span> ปี</div>
  <div class="row">ที่อยู่ <span class="f" style="min-width:420px;">{_val(d.get("address"))}</span>
    โทรศัพท์ <span class="f" style="min-width:130px;">{_val(d.get("phone"))}</span></div>
  <div class="row">สิทธิการรักษาพยาบาล {rights}
    <span class="f" style="min-width:120px;">{_val(d.get("right_other"))}</span>
    สถานพยาบาล <span class="f" style="min-width:200px;">{_val(d.get("hospital"))}</span></div>
  <div class="row">เหตุผลในการส่งต่อ {reasons}</div>

  <div class="cols">
    <div class="col">
      <div class="colhead">การทบทวนรายการยาและผลิตภัณฑ์สุขภาพ</div>
      <div class="colbody">{_multiline(d.get("med_review"), 14)}</div>
    </div>
    <div class="col">
      <div class="colhead">รายละเอียดของการส่งต่อ</div>
      <div class="colbody">
        <div class="lbl">อาการสำคัญ:</div>{_multiline(d.get("chief_complaint"), BLANK_LINES)}
        <div class="lbl">ประวัติการเจ็บป่วย:</div>{_multiline(d.get("illness_history"), BLANK_LINES)}
        <div class="lbl">โรคประจำตัว:</div>{_multiline(d.get("chronic_disease"), BLANK_LINES)}
        <div class="lbl">ประวัติแพ้ยา:</div>{_multiline(d.get("allergy_history"), BLANK_LINES)}
        <div class="lbl">ข้อมูลเพิ่มเติม:</div>{_multiline(d.get("extra_info"), BLANK_LINES)}
        <div class="lbl">ปัญหาที่พบ:</div>{_multiline(d.get("problem_found"), 3)}
      </div>
    </div>
  </div>

  <div class="band">การดำเนินการเบื้องต้นของเภสัชกร</div>
  <div class="row">{_chk(d.get("act_treat"))} ให้การรักษาเบื้องต้น ได้แก่
    <span class="f" style="min-width:470px;">{_val(d.get("act_treat_text"))}</span></div>
  <div class="row">{_chk(d.get("act_advice"))} ให้คำแนะนำการใช้ยา/การดูแลตนเอง ดังนี้
    <span class="f" style="min-width:400px;">{_val(d.get("act_advice_text"))}</span></div>
  <div class="row">{_chk(d.get("act_other"))} อื่นๆ
    <span class="f" style="min-width:560px;">{_val(d.get("act_other_text"))}</span></div>

  <div class="sign">
    (ลงชื่อ)<br>
    เภสัชกรผู้ส่งต่อ <span class="f" style="min-width:300px;">{_val(d.get("pharmacist_name"))}</span><br>
    เลขที่ใบประกอบวิชาชีพ <span class="f" style="min-width:260px;">{_val(d.get("license_no"))}</span><br>
    วันที่ <span class="f" style="min-width:300px;">{_val(_fmt_date(d.get("referred_at")))}</span>
  </div>
  <div class="shop">
    ร้านยา <span class="f" style="min-width:430px;">{_val(d.get("shop_name"))}</span>
    โทรศัพท์ <span class="f" style="min-width:150px;">{_val(d.get("shop_phone"))}</span><br>
    ที่อยู่ <span class="f" style="min-width:640px;">{_val(d.get("shop_address"))}</span>
  </div>
</div>
"""


def _prf_sheet(d):
    return f"""
<div class="sheet">
  <div class="hdr">
    <div class="org">สภช CPA</div>
    <div class="org">สปสช.</div>
    <div class="cid">รหัสประจำตัวประชาชน {_cid_boxes(d.get("citizen_id"))}</div>
  </div>
  <div class="title">ใบตอบกลับการส่งต่อ (Physician Responded Form: PRF)
    <span class="copytag">ให้โรงพยาบาลกรอกส่งกลับ</span></div>

  <div class="row">วันที่ <span class="f" style="min-width:200px;">&nbsp;</span></div>
  <div class="row"><b>ถึง เภสัชกรผู้เกี่ยวข้อง</b></div>
  <div class="row" style="margin-top:10px;">ตามที่ท่านได้ส่งผู้ป่วย ชื่อ
    <span class="f" style="min-width:330px;">{_val(d.get("patient_name"))}</span>
    เพศ <span class="f" style="min-width:70px;">{_val(d.get("gender"))}</span>
    อายุ <span class="f" style="min-width:45px;">{_val(d.get("age"))}</span> ปี</div>
  <div class="row">มาเพื่อดำเนินการตามประสงค์นั้น ขอส่งรายละเอียดมาเพื่อทราบ ดังนี้</div>

  <div class="lbl" style="margin-top:12px;">ผลการตรวจวินิจฉัย</div>
  {_multiline("", 6)}
  <div class="lbl" style="margin-top:14px;">การรักษาที่ให้</div>
  {_multiline("", 6)}
  <div class="lbl" style="margin-top:14px;">สิ่งที่ผู้ป่วยควรได้รับการดูแลหรือติดตามอย่างต่อเนื่อง</div>
  {_multiline("", 6)}

  <div class="sign">
    (ลงชื่อ)<br>
    แพทย์ประจำตัวผู้ป่วย <span class="f" style="min-width:290px;">&nbsp;</span><br>
    สถานพยาบาล <span class="f" style="min-width:330px;">&nbsp;</span><br>
    เบอร์โทรติดต่อ <span class="f" style="min-width:310px;">&nbsp;</span>
  </div>
</div>
"""


def _fmt_date(v):
    if not v:
        return ""
    s = str(v)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return s


def render_referral_html(d):
    """Three sheets: the shop's copy, the patient's copy, and the blank reply
    form the patient carries to the hospital."""
    body = (_phrf_sheet(d, "ฉบับร้านยา (เก็บไว้)")
            + _phrf_sheet(d, "ฉบับผู้ป่วย")
            + _prf_sheet(d))
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ใบส่งต่อผู้ป่วย - {_esc(d.get('patient_name'))}</title>
<style>{CSS}</style></head>
<body>
  <div class="no-print" style="margin-bottom:8px;">
    <button onclick="window.print()" style="padding:6px 14px;font-weight:bold;">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
    <span style="font-size:11px;color:#666;margin-left:8px;">3 แผ่น: ฉบับร้านยา / ฉบับผู้ป่วย / ใบตอบกลับ</span>
  </div>
  {body}
</body></html>"""


def open_referral_report(d):
    path = os.path.join(tempfile.gettempdir(), f"referral_{uuid.uuid4().hex[:8]}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_referral_html(d))
    webbrowser.open(f"file:///{path.replace(os.sep, '/')}")


# ── UI ───────────────────────────────────────────────────────────────────────


class ReferralMixin:
    """Mixed into LabelApp. Needs self.root."""

    def open_referral_dialog(self, patient_id=None):
        """The list of past referrals plus a button to write a new one."""
        ensure_referrals_table()
        win = tk.Toplevel(self.root)
        win.title("ใบส่งต่อผู้ป่วย (ข.ย. / สปสช.)")
        h = min(fs(620), max(fs(420), win.winfo_screenheight() - fs(80)))
        win.geometry(f"{fs(900)}x{h}")
        win.transient(self.root)

        bar = tk.Frame(win)
        bar.pack(fill="x", padx=fs(10), pady=fs(8))
        tk.Button(bar, text="➕ เขียนใบส่งต่อใหม่", font=("Tahoma", fs(11), "bold"),
                  bg="#1a5a9a", fg="white",
                  command=lambda: self.open_referral_form(win, on_saved=refresh)).pack(side="left")
        tk.Label(bar, text="ค้นหาชื่อ", font=("Tahoma", fs(10))).pack(side="left", padx=(fs(12), fs(4)))
        term_var = tk.StringVar()
        tk.Entry(bar, textvariable=term_var, font=("Tahoma", fs(10)), width=22).pack(side="left")
        tk.Button(bar, text="ค้นหา", font=("Tahoma", fs(10)),
                  command=lambda: refresh()).pack(side="left", padx=fs(4))

        cols = ("date", "name", "hospital", "cc")
        headers = {"date": "วันที่ส่งต่อ", "name": "ชื่อผู้ป่วย",
                   "hospital": "สถานพยาบาลปลายทาง", "cc": "อาการสำคัญ"}
        widths = {"date": 130, "name": 200, "hospital": 200, "cc": 320}
        wrap = tk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=fs(10))
        tree = ttk.Treeview(wrap, columns=cols, show="headings", height=14)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=fs(widths[c]), anchor="w")
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        rows = {}

        def refresh():
            for i in tree.get_children():
                tree.delete(i)
            rows.clear()
            try:
                items = list_referrals(term_var.get())
            except Exception as e:
                messagebox.showerror("ผิดพลาด", f"โหลดไม่สำเร็จ: {e}", parent=win)
                return
            for it in items:
                # referred_at is a plain date here (SQLite), so there is no
                # time part to tack on the way the SQL Server build does
                iid = tree.insert("", "end", values=(
                    _fmt_date(it["referred_at"]),
                    it["patient_name"], it["hospital"], it["chief_complaint"][:60]))
                rows[iid] = it["id"]

        def reprint():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("แจ้งเตือน", "เลือกรายการก่อน", parent=win)
                return
            d = get_referral(rows[sel[0]])
            if d:
                d.update(self._shop_details())
                open_referral_report(d)

        def remove():
            sel = tree.selection()
            if not sel:
                return
            if not messagebox.askyesno("ยืนยันลบ", "ลบใบส่งต่อนี้ออกจากระบบ?", parent=win):
                return
            delete_referral(rows[sel[0]])
            refresh()

        act = tk.Frame(win)
        act.pack(fill="x", padx=fs(10), pady=fs(8))
        tk.Button(act, text="🖨️ พิมพ์ซ้ำ", font=("Tahoma", fs(10), "bold"),
                  command=reprint).pack(side="left")
        tk.Button(act, text="🗑 ลบ", font=("Tahoma", fs(10)), command=remove).pack(side="left", padx=fs(6))
        tk.Label(act, text="เก็บไว้ให้เจ้าหน้าที่ตรวจย้อนหลังได้ - พิมพ์ซ้ำได้ทุกเมื่อ",
                 font=("Tahoma", fs(9)), fg="#555").pack(side="left", padx=fs(10))

        tree.bind("<Double-Button-1>", lambda e: reprint())
        refresh()
        if patient_id:
            self.open_referral_form(win, patient_id=patient_id, on_saved=refresh)
        win.lift()
        win.focus_force()

    def _shop_details(self):
        """Shop name/phone/address for the footer - the same block already
        printed on every drug label, so this works out of the box with
        nothing extra to fill in."""
        return shop_defaults()

    def open_referral_form(self, parent, patient_id=None, on_saved=None):
        """The form itself. Anything already on file for the patient is
        pre-filled - name, phone, allergy note and the drugs dispensed
        recently - so only the clinical part has to be typed."""
        win = tk.Toplevel(parent)
        win.title("เขียนใบส่งต่อผู้ป่วย")
        h = min(fs(760), max(fs(460), win.winfo_screenheight() - fs(70)))
        win.geometry(f"{fs(760)}x{h}")
        win.transient(parent)
        win.grab_set()

        outer = tk.Frame(win)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        cw = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cw, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        pad = {"padx": fs(12), "pady": fs(2)}
        V = {}

        def entry(label, key, width=None, value=""):
            tk.Label(body, text=label, font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
            V[key] = tk.StringVar(value=value)
            e = tk.Entry(body, textvariable=V[key], font=("Tahoma", fs(11)))
            e.pack(fill="x", **pad) if width is None else e.pack(anchor="w", **pad)
            if width:
                e.config(width=width)
            return e

        # Whichever multi-line box was last clicked into is where the symptom
        # buttons insert - so the picker serves อาการสำคัญ by default but works
        # for ประวัติการเจ็บป่วย, ปัญหาที่พบ and the rest too.
        focus_ref = {"widget": None}
        text_boxes = []          # (ชื่อช่อง, widget) - feeds the "ใส่ลงช่อง" dropdown
        target_var = tk.StringVar(value="อาการสำคัญ")

        def text(label, key, height=3, value=""):
            tk.Label(body, text=label, font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
            t = tk.Text(body, font=("Tahoma", fs(11)), height=height)
            t.insert("1.0", value or "")
            t.pack(fill="x", **pad)
            name = label.replace("*", "").strip()
            text_boxes.append((name, t))

            def on_focus(_e, w=t, n=name):
                focus_ref["widget"] = w
                target_var.set(n)      # keep the dropdown showing where text will land

            t.bind("<FocusIn>", on_focus)
            V[key] = t
            return t

        # ---- prefill from the patient file -------------------------------
        pat = None
        if patient_id:
            try:
                pat = storage.get_patient(patient_id)
            except Exception:
                pat = None
        drugs_seen = ""
        if pat:
            try:
                jobs = storage.list_print_jobs_for_patient(
                    pat["name"], pat.get("phone", ""), patient_id=patient_id, limit=5)
                names = []
                for j in jobs:
                    for dd in j.get("drugs", []):
                        n = dd.get("drug1", "")
                        if n and n not in names:
                            names.append(n)
                drugs_seen = "\n".join(names[:15])
            except Exception:
                drugs_seen = ""

        tk.Label(body, text="ข้อมูลผู้ป่วย", font=("Tahoma", fs(12), "bold"),
                 fg="#1a5a9a").pack(anchor="w", padx=fs(12), pady=(fs(10), fs(2)))
        name_e = entry("ชื่อ-สกุลผู้ป่วย *  (พิมพ์ชื่อ/เบอร์/HN แล้วเลือกจากรายการ)",
                       "patient_name", value=(pat or {}).get("name", ""))

        # Live lookup against แฟ้มผู้ป่วย, restricted to records that have an
        # HN - those are the ones with a real file behind them, and picking one
        # is what pulls in the phone, allergy note and recent drugs below.
        sugg = tk.Listbox(body, font=("Tahoma", fs(10)), height=5)
        sugg_hits = []
        picked = {"id": patient_id}

        def hide_sugg():
            sugg.pack_forget()
            sugg.delete(0, tk.END)

        def on_name_typed(*_a):
            term = V["patient_name"].get().strip()
            if len(term) < 2:
                hide_sugg()
                return
            try:
                hits = [h for h in storage.search_patients(term, limit=40)
                        if (h.get("hn_code") or "").strip()]
            except Exception:
                hits = []
            sugg_hits[:] = hits[:10]
            sugg.delete(0, tk.END)
            if not sugg_hits:
                hide_sugg()
                return
            for h in sugg_hits:
                bits = [h["name"]]
                if h.get("phone"):
                    bits.append(h["phone"])
                bits.append("HN " + h["hn_code"])
                sugg.insert(tk.END, "  ·  ".join(bits))
            # after=name_e keeps it under the name box; a plain pack() would
            # re-append it to the bottom of the form each time it reappears.
            sugg.pack(fill="x", padx=fs(12), after=name_e)

        def on_pick(_e=None):
            sel = sugg.curselection()
            if not sel:
                return
            h = sugg_hits[sel[0]]
            picked["id"] = h["id"]
            V["patient_name"].set(h["name"])
            V["phone"].set(h.get("phone", ""))
            allergy = V.get("allergy_history")
            if allergy is not None and not allergy.get("1.0", "end").strip():
                allergy.insert("1.0", h.get("allergy_note", ""))
            mr = V.get("med_review")
            if mr is not None and not mr.get("1.0", "end").strip():
                try:
                    jobs = storage.list_print_jobs_for_patient(
                        h["name"], h.get("phone", ""), patient_id=h["id"], limit=5)
                    names = []
                    for j in jobs:
                        for dd in j.get("drugs", []):
                            n = dd.get("drug1", "")
                            if n and n not in names:
                                names.append(n)
                    mr.insert("1.0", "\n".join(names[:15]))
                except Exception:
                    pass
            hide_sugg()

        sugg.bind("<<ListboxSelect>>", on_pick)
        V["patient_name"].trace_add("write", on_name_typed)

        row = tk.Frame(body)
        row.pack(fill="x", **pad)
        tk.Label(row, text="เพศ", font=("Tahoma", fs(10), "bold")).pack(side="left")
        V["gender"] = tk.StringVar()
        ttk.Combobox(row, textvariable=V["gender"], values=["ชาย", "หญิง"], state="readonly",
                     width=8, font=("Tahoma", fs(10))).pack(side="left", padx=fs(6))
        tk.Label(row, text="อายุ (ปี)", font=("Tahoma", fs(10), "bold")).pack(side="left", padx=(fs(10), 0))
        V["age"] = tk.StringVar()
        tk.Entry(row, textvariable=V["age"], font=("Tahoma", fs(11)), width=6).pack(side="left", padx=fs(6))
        tk.Label(row, text="เลขบัตรประชาชน", font=("Tahoma", fs(10), "bold")).pack(side="left", padx=(fs(10), 0))
        V["citizen_id"] = tk.StringVar()
        tk.Entry(row, textvariable=V["citizen_id"], font=("Tahoma", fs(11)), width=20).pack(side="left", padx=fs(6))

        entry("ที่อยู่", "address")
        entry("โทรศัพท์", "phone", width=24, value=(pat or {}).get("phone", ""))

        row2 = tk.Frame(body)
        row2.pack(fill="x", **pad)
        tk.Label(row2, text="สิทธิการรักษา", font=("Tahoma", fs(10), "bold")).pack(side="left")
        V["right_type"] = tk.StringVar(value=DEFAULT_RIGHT_TYPE)
        ttk.Combobox(row2, textvariable=V["right_type"], values=RIGHT_OPTIONS, state="readonly",
                     width=12, font=("Tahoma", fs(10))).pack(side="left", padx=fs(6))
        V["right_other"] = tk.StringVar()
        tk.Entry(row2, textvariable=V["right_other"], font=("Tahoma", fs(11)), width=18).pack(side="left")
        tk.Label(row2, text="(ระบุ ถ้าเลือกอื่นๆ)", font=("Tahoma", fs(8)), fg="#777").pack(side="left", padx=fs(4))

        _st = app_settings.load_settings() or {}
        entry("สถานพยาบาลที่ส่งต่อ", "hospital",
              value=_st.get("default_referral_hospital")
              or "โรงพยาบาลสมเด็จพระยุพราชเวียงสระ")

        tk.Label(body, text="เหตุผลในการส่งต่อ", font=("Tahoma", fs(12), "bold"),
                 fg="#1a5a9a").pack(anchor="w", padx=fs(12), pady=(fs(10), fs(2)))
        for key, label in REASON_OPTIONS:
            V["reason_" + key] = tk.BooleanVar()
            tk.Checkbutton(body, text=label, variable=V["reason_" + key],
                           font=("Tahoma", fs(10))).pack(anchor="w", padx=fs(18))

        tk.Label(body, text="รายละเอียดการส่งต่อ", font=("Tahoma", fs(12), "bold"),
                 fg="#1a5a9a").pack(anchor="w", padx=fs(12), pady=(fs(10), fs(2)))
        text("อาการสำคัญ *", "chief_complaint", 5)

        # ---- symptom picker ------------------------------------------------
        pick_fr = tk.LabelFrame(body, text=" เลือกกลุ่มอาการ (กดเพื่อเติมลงช่องที่เลือกไว้) ",
                                font=("Tahoma", fs(9), "bold"), padx=fs(8), pady=fs(4))
        pick_fr.pack(fill="x", padx=fs(12), pady=fs(4))
        grp_row = tk.Frame(pick_fr)
        grp_row.pack(fill="x")
        tk.Label(grp_row, text="กลุ่มอาการ", font=("Tahoma", fs(10), "bold")).pack(side="left")
        grp_var = tk.StringVar()
        grp_names = [g for g, _ in SYMPTOM_GROUPS]
        ttk.Combobox(grp_row, textvariable=grp_var, values=grp_names, state="readonly",
                     width=44, font=("Tahoma", fs(10))).pack(side="left", padx=fs(6))

        tgt_row = tk.Frame(pick_fr)
        tgt_row.pack(fill="x", pady=(fs(3), 0))
        tk.Label(tgt_row, text="ใส่ลงช่อง", font=("Tahoma", fs(10), "bold")).pack(side="left")
        tgt_cb = ttk.Combobox(tgt_row, textvariable=target_var, state="readonly",
                              width=44, font=("Tahoma", fs(10)))
        tgt_cb.pack(side="left", padx=fs(6))

        btn_fr = tk.Frame(pick_fr)
        btn_fr.pack(fill="x", pady=(fs(4), 0))

        def target_widget():
            """The box the buttons write into - the one picked in the dropdown,
            which also follows whichever box was last clicked."""
            for name, w in text_boxes:
                if name == target_var.get():
                    return w
            return focus_ref["widget"] or V["chief_complaint"]

        def on_target_pick(_e=None):
            w = target_widget()
            focus_ref["widget"] = w
            w.focus_set()

        tgt_cb.bind("<<ComboboxSelected>>", on_target_pick)

        def insert_symptom(s):
            """Insert at the cursor, spaced off from whatever is already on
            either side - inserting in the middle of existing text needs a gap
            after as well as before, not just before."""
            w = target_widget()
            pos = w.index("insert")
            before = w.get("1.0", pos)
            after = w.get(pos, "end-1c")
            lead = "" if (not before.strip() or before.endswith((" ", "\n"))) else " "
            trail = "" if (not after.strip() or after.startswith((" ", "\n"))) else " "
            w.insert(pos, lead + s + trail)
            w.focus_set()
            w.mark_set("insert", f"{pos} + {len(lead) + len(s)} chars")

        def show_group(_e=None):
            for c in btn_fr.winfo_children():
                c.destroy()
            items = dict(SYMPTOM_GROUPS).get(grp_var.get(), [])
            if not items:
                tk.Label(btn_fr, text="(กลุ่มนี้ยังไม่ได้ใส่รายการอาการ)",
                         font=("Tahoma", fs(9)), fg="#777").pack(anchor="w")
                return
            for i, s in enumerate(items):
                tk.Button(btn_fr, text=s, font=("Tahoma", fs(9)), anchor="w",
                          justify="left", wraplength=fs(330),
                          command=lambda s=s: insert_symptom(s)).grid(
                    row=i // 2, column=i % 2, sticky="ew", padx=fs(2), pady=fs(1))
            btn_fr.columnconfigure(0, weight=1)
            btn_fr.columnconfigure(1, weight=1)

        grp_var.trace_add("write", lambda *a: show_group())
        show_group()
        text("ประวัติการเจ็บป่วย", "illness_history", 3)
        text("โรคประจำตัว", "chronic_disease", 2)
        text("ประวัติแพ้ยา", "allergy_history", 2, value=(pat or {}).get("allergy_note", ""))
        text("ข้อมูลเพิ่มเติม", "extra_info", 2)
        text("ปัญหาที่พบ", "problem_found", 3)
        text("การทบทวนรายการยาและผลิตภัณฑ์สุขภาพ", "med_review", 6, value=drugs_seen)

        # the boxes below the picker only exist now, so fill its list in here
        tgt_cb.config(values=[n for n, _ in text_boxes])

        tk.Label(body, text="การดำเนินการเบื้องต้นของเภสัชกร", font=("Tahoma", fs(12), "bold"),
                 fg="#1a5a9a").pack(anchor="w", padx=fs(12), pady=(fs(10), fs(2)))
        for key, label in (("act_treat", "ให้การรักษาเบื้องต้น ได้แก่"),
                           ("act_advice", "ให้คำแนะนำการใช้ยา/การดูแลตนเอง ดังนี้"),
                           ("act_other", "อื่นๆ")):
            V[key] = tk.BooleanVar()
            tk.Checkbutton(body, text=label, variable=V[key],
                           font=("Tahoma", fs(10))).pack(anchor="w", padx=fs(18))
            V[key + "_text"] = tk.StringVar()
            tk.Entry(body, textvariable=V[key + "_text"], font=("Tahoma", fs(11))).pack(
                fill="x", padx=fs(30), pady=(0, fs(4)))

        tk.Label(body, text="ผู้ส่งต่อ", font=("Tahoma", fs(12), "bold"),
                 fg="#1a5a9a").pack(anchor="w", padx=fs(12), pady=(fs(10), fs(2)))
        # Editable dropdown of the names already printed on the label, so the
        # pharmacist on duty is one click - still free-text for anyone else.
        names = pharmacist_choices()
        tk.Label(body, text="ชื่อเภสัชกรผู้ส่งต่อ",
                 font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
        V["pharmacist_name"] = tk.StringVar(
            value=_st.get("pharmacist_name") or (names[0] if names else ""))
        ttk.Combobox(body, textvariable=V["pharmacist_name"], values=names,
                     font=("Tahoma", fs(11))).pack(fill="x", **pad)
        entry("เลขที่ใบประกอบวิชาชีพ", "license_no", width=28,
              value=_st.get("license_no", ""))
        row3 = tk.Frame(body)
        row3.pack(anchor="w", **pad)
        tk.Label(row3, text="วันที่ส่งต่อ", font=("Tahoma", fs(10), "bold")).pack(side="left")
        V["referred_at"] = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        tk.Entry(row3, textvariable=V["referred_at"], font=("Tahoma", fs(11)),
                 width=14).pack(side="left", padx=fs(6))
        tk.Label(row3, text="(วัน/เดือน/ปี ค.ศ. เช่น 31/07/2026)", font=("Tahoma", fs(8)),
                 fg="#777").pack(side="left")

        status = tk.StringVar()
        tk.Label(body, textvariable=status, font=("Tahoma", fs(10)), fg="#b91c1c").pack(
            anchor="w", padx=fs(12), pady=fs(4))
        tk.Frame(body, height=fs(8)).pack()

        def collect():
            out = {}
            for k, v in V.items():
                if isinstance(v, tk.Text):
                    out[k] = v.get("1.0", "end").strip()
                elif isinstance(v, tk.BooleanVar):
                    out[k] = bool(v.get())
                else:
                    out[k] = v.get().strip()
            d = storage._parse_date_flexible(out.get("referred_at", ""))
            out["referred_at"] = d.isoformat() if d else datetime.now().date().isoformat()
            # whichever patient was picked from the lookup, else the one this
            # form was opened for
            out["patient_id"] = picked.get("id") or patient_id
            return out

        def on_save(and_print):
            data = collect()
            if not data.get("patient_name"):
                status.set("กรุณากรอกชื่อผู้ป่วย")
                return
            if not data.get("chief_complaint"):
                status.set("กรุณากรอกอาการสำคัญ")
                return
            try:
                save_referral(data)
            except Exception as e:
                status.set(f"บันทึกไม่สำเร็จ: {e}")
                return
            if and_print:
                data.update(self._shop_details())
                open_referral_report(data)
            win.destroy()
            if on_saved:
                on_saved()

        btns = tk.Frame(win)
        btns.pack(fill="x", pady=fs(8))
        tk.Button(btns, text="💾 บันทึก + พิมพ์", font=("Tahoma", fs(11), "bold"),
                  bg="#1a7a4a", fg="white",
                  command=lambda: on_save(True)).pack(side="left", padx=fs(12))
        tk.Button(btns, text="บันทึกอย่างเดียว", font=("Tahoma", fs(11)),
                  command=lambda: on_save(False)).pack(side="left")
        tk.Button(btns, text="ยกเลิก", font=("Tahoma", fs(11)),
                  command=win.destroy).pack(side="right", padx=fs(12))

        win.lift()
        win.focus_force()
        name_e.focus_set()
