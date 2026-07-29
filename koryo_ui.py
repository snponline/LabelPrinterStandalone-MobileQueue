"""ข.ย.9 / ข.ย.11 reporting UI for LabelPrinterStandalone_MobileQueue.

Ported from HOPE label_printer, talking to local SQLite via storage.py rather
than SQL Server. Same three-tab report screen, same printed sheets (one page
per drug), same corrections; a drug is identified by drug_templates.id
(template_id) because this build has no POS catalog to key against.

Kept in its own module for the same reason warranty_ui.py is - label_gui.py is
already ~5k lines and this is another ~700.
"""
from __future__ import annotations

import html
import os
import tempfile
import uuid
import webbrowser
from datetime import date, datetime

import tkinter as tk
from tkinter import messagebox, ttk

import storage

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

THAI_MONTH_NAMES = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

ALL_DRUGS_LABEL = "— ทุกตัวยา —"

DRUG_REPORT_CATEGORIES = [
    ("none", "ไม่ต้องรายงาน"),
    ("dangerous", "ยาอันตราย (ข.ย.11)"),
    ("tramadol", "สูตรผสม tramadol (ข.ย.11)"),
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


# ── dates ────────────────────────────────────────────────────────────────────


def parse_date_flexible(text):
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


def format_date_dmy(d):
    """DD/MM/YY - the short form used inside entry fields."""
    if d is None:
        return ""
    try:
        if hasattr(d, "strftime"):
            return d.strftime("%d/%m/%y")
        parsed = parse_date_flexible(str(d))
        return parsed.strftime("%d/%m/%y") if parsed else str(d)[:10]
    except Exception:
        return str(d)[:10]


def ky_date_dmy(value):
    """'2026-07-29' -> '29/07/2026'. Fixed 10 chars, so a date column can be
    sized once and never wrap."""
    parsed = parse_date_flexible(str(value or "")[:10])
    return parsed.strftime("%d/%m/%Y") if parsed else ky_html_escape(value)


def ky_date_label(from_text, to_text):
    d_from = parse_date_flexible(from_text)
    d_to = parse_date_flexible(to_text)
    if not d_from and not d_to:
        return "ทั้งหมด"
    return f"{format_date_dmy(d_from) if d_from else '-'} ถึง {format_date_dmy(d_to) if d_to else '-'}"


# ── printed sheets ───────────────────────────────────────────────────────────


def ky_html_escape(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _wrap_ky_report_html(title, date_label, body_html, landscape, ky9):
    """Standalone HTML wrapper - opened in the default browser, which is the
    simplest reliable path to paper for an app whose own printing pipeline is
    pixel-image based, not table layout."""
    printed_at = datetime.now().strftime("%d/%m/%y %H:%M")
    page_size = "A4 landscape" if landscape else "A4"
    page_margin = "0.8cm 0.7cm" if landscape else "1.2cm 1cm"
    ky9_css = """
        table.ky9-print-table { font-size:13.5px; }
        table.ky9-print-table th, table.ky9-print-table td { padding:4px 5px; }
        .ky9-seq, .ky9-date, .ky9-lot, .ky9-exp, .ky9-qty, .ky9-unit { white-space:nowrap; }
        .ky9-seller { word-wrap:break-word; overflow-wrap:anywhere; }
        .ky9-seq { width:4%; } .ky9-date { width:10%; } .ky9-seller { width:28%; }
        .ky9-lot { width:11%; } .ky9-exp { width:10%; }
        .ky9-qty { width:7%; } .ky9-unit { width:8%; }
        .ky9-sign { width:15%; } .ky9-note { width:7%; }
    """ if ky9 else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{ky_html_escape(title)}</title>
<style>
  @page {{ size: {page_size}; margin: {page_margin}; }}
  body {{ font-family: Tahoma, sans-serif; color:#111; margin:0; padding:16px; }}
  h1 {{ font-size:{'15px' if landscape else '18px'}; font-weight:800; margin:0 0 4px; }}
  .meta {{ font-size:11px; color:#444; margin:0 0 10px; }}
  table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-size:{'10.5px' if landscape else '12px'}; margin:0 0 10px; }}
  th, td {{ border:1px solid #ccc; padding:3px 5px; vertical-align:middle; word-wrap:break-word; overflow-wrap:anywhere; }}
  th {{ background:#eef1f4; font-weight:700; text-align:left; }}
  td.num, th.num {{ text-align:right; white-space:nowrap; }}
  .nw {{ white-space:nowrap; }}
  tr {{ page-break-inside:avoid; }} thead {{ display:table-header-group; }}
  {ky9_css}
  .footer {{ margin-top:10px; font-size:10px; color:#666; border-top:1px solid #ddd; padding-top:4px; }}
  @media print {{ .no-print {{ display:none !important; }} }}
</style>
</head>
<body>
  <div class="no-print" style="margin-bottom:10px;">
    <button onclick="window.print()" style="padding:6px 14px;font-weight:bold;">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
  </div>
  <h1>{ky_html_escape(title)}</h1>
  <div class="meta">ช่วงวันที่: {ky_html_escape(date_label or '-')} &nbsp;|&nbsp; พิมพ์เมื่อ: {printed_at}</div>
  {body_html}
  <div class="footer">LabelPrinter — รายงานนี้สร้างจากหน้าจอ 📋 รายงาน ข.ย.9/11</div>
</body></html>"""


def render_ky9_sheet_html(sheet):
    """One drug's ข.ย.9 sheet. ชื่อยา sits in the header line since every row
    on the sheet is the same drug."""
    trs = "".join(
        f"<tr><td>{r['seq']}</td><td>{ky_date_dmy(r['date'])}</td>"
        f"<td>{ky_html_escape(r['seller'])}</td>"
        f"<td>{ky_html_escape(r['lot_number'])}</td>"
        f"<td>{ky_date_dmy(r.get('exp_date', ''))}</td>"
        f"<td class=\"num\">{ky_html_escape(r['qty'])}</td>"
        f"<td>{ky_html_escape(r['unit_name'])}</td><td></td><td></td></tr>"
        for r in sheet["rows"]
    )
    sources = " · ".join(sheet["sources"]) if sheet["sources"] else "-"
    return f"""
    <div style="border:1px solid #ccc;padding:12px;margin-bottom:20px;background:#fff;">
      <div style="font-weight:800;margin-bottom:6px;">แบบ ข.ย.9 บัญชีการซื้อยา</div>
      <div style="font-size:0.95em;line-height:1.7;margin-bottom:6px;">
        ชื่อยา: <b>{ky_html_escape(sheet['product_name'])}</b>
        &nbsp;|&nbsp; หน่วย: {ky_html_escape(sheet['unit_name'] or '-')}
        &nbsp;|&nbsp; ซื้อจาก: {ky_html_escape(sources)}
      </div>
      <table class="ky9-print-table"><thead><tr>
        <th class="ky9-seq">ลำดับ</th><th class="ky9-date">วันที่ซื้อ</th>
        <th class="ky9-seller">ชื่อผู้ขาย</th>
        <th class="ky9-lot">Lot</th><th class="ky9-exp">วันหมดอายุ</th>
        <th class="num ky9-qty">จำนวน</th>
        <th class="ky9-unit">หน่วย</th><th class="ky9-sign">ลายเซ็นผู้มีหน้าที่ปฏิบัติการ</th>
        <th class="ky9-note">หมายเหตุ</th>
      </tr></thead><tbody>{trs}</tbody></table>
    </div>
    """


def render_ky9_print_html(sheets, date_label):
    """One page per drug (page-break-after between sheets)."""
    if not sheets:
        body = '<p style="color:#666;">ไม่มีข้อมูลให้พิมพ์ - กด "แสดงรายงาน" ก่อน</p>'
    else:
        body = '<div style="page-break-after:always;"></div>'.join(
            render_ky9_sheet_html(s) for s in sheets
        )
    return _wrap_ky_report_html("แบบ ข.ย.9 บัญชีการซื้อยา", date_label, body, landscape=True, ky9=True)


def render_ky11_sheet_html(sheet, category):
    """One drug's ข.ย.11 sheet - header line, lot summary, then the sales
    table. Fixed-format columns are squeezed and pinned nowrap so the buyer
    block (which for tramadol is name / citizen ID / address) gets the page."""
    lots_html = ""
    if sheet["lots"]:
        unit_suffix = f" {ky_html_escape(sheet['unit_name'])}" if sheet["unit_name"] else ""
        lot_rows = "".join(
            f"<tr><td>{ky_html_escape(lt['lot_number'] or '-')}</td>"
            f"<td class=\"nw\">{ky_date_dmy(lt['received_date'])}</td>"
            f"<td class=\"num\">{lt['qty_received']:g}{unit_suffix}</td>"
            f"<td>{ky_html_escape(lt['source'] or '-')}</td></tr>"
            for lt in sheet["lots"]
        )
        lots_html = (
            '<table style="margin:6px 0 10px;font-size:0.92em;"><thead><tr>'
            "<th>Lot</th><th>วันที่รับ</th><th class=\"num\">จำนวนรับ</th><th>ได้มาจาก</th>"
            f"</tr></thead><tbody>{lot_rows}</tbody></table>"
        )
    sales_rows = "".join(
        f"<tr><td class=\"nw\">{s['seq']}</td><td class=\"nw\">{ky_date_dmy(s['date'])}</td>"
        f"<td class=\"num\">{float(s['qty']):g}</td><td class=\"nw\">{ky_html_escape(sheet['unit_name'])}</td>"
        f"<td class=\"nw\">{ky_html_escape(s['lot_number'])}</td>"
        f"<td style=\"white-space:pre-line;line-height:1.35;\">"
        f"{ky_html_escape(s['buyer_block']).replace(chr(10), '<br>')}</td>"
        f"<td></td><td></td></tr>"
        for s in sheet["sales"]
    ) or '<tr><td colspan="8" style="color:#666;">ไม่มีรายการขาย</td></tr>'
    sources = " · ".join(sheet["sources"]) if sheet["sources"] else "-"
    return f"""
    <div style="border:1px solid #ccc;padding:12px;margin-bottom:20px;background:#fff;">
      <div style="font-weight:800;margin-bottom:6px;">แบบ ข.ย.11 บัญชีการขายยาอันตราย</div>
      <div style="font-size:0.95em;line-height:1.7;margin-bottom:4px;">
        ชื่อยา: <b>{ky_html_escape(sheet['product_name'])}</b>
        &nbsp;|&nbsp; หน่วย: {ky_html_escape(sheet['unit_name'] or '-')}
        &nbsp;|&nbsp; แหล่งซื้อ: {ky_html_escape(sources)}
      </div>
      {lots_html}
      <table>
        <thead><tr>
          <th class="nw" style="width:3.5%;">#</th><th class="nw" style="width:10%;">วันที่ขาย</th>
          <th class="nw num" style="width:7%;">จำนวน</th><th class="nw" style="width:9%;">หน่วย</th>
          <th class="nw" style="width:9%;">Lot</th><th style="width:40%;">ชื่อ-สกุลผู้ซื้อ</th>
          <th style="width:14.5%;">ลายเซ็นผู้มีหน้าที่ปฏิบัติการ</th><th style="width:7%;">หมายเหตุ</th>
        </tr></thead>
        <tbody>{sales_rows}</tbody>
      </table>
    </div>
    """


def render_ky11_print_html(sheets, category, date_label):
    title = "แบบ ข.ย.11 บัญชีการขายยาอันตราย" + (
        " (สูตรผสม tramadol)" if category == "tramadol" else " (ยาอันตราย ที่ อย. กำหนดเฉพาะ)"
    )
    if not sheets:
        body = '<p style="color:#666;">ไม่มีข้อมูลให้พิมพ์ - กด "แสดงรายงาน" ก่อน</p>'
    else:
        body = '<div style="page-break-after:always;"></div>'.join(
            render_ky11_sheet_html(s, category) for s in sheets
        )
    return _wrap_ky_report_html(title, date_label, body, landscape=False, ky9=False)


def open_html_report(html_out, filename_prefix):
    path = os.path.join(tempfile.gettempdir(), f"{filename_prefix}_{uuid.uuid4().hex[:8]}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
    webbrowser.open(f"file:///{path.replace(os.sep, '/')}")


# ── UI ───────────────────────────────────────────────────────────────────────


class KoryoMixin:
    """Mixed into LabelApp. Only needs `self.root` from the host class."""

    # -- shared bits ---------------------------------------------------------

    def open_date_picker(self, parent_widget, target_var):
        win = tk.Toplevel(parent_widget)
        win.title("เลือกวันที่")
        win.transient(parent_widget)
        win.grab_set()

        initial = parse_date_flexible(target_var.get()) or datetime.now().date()
        state = {"year": initial.year, "month": initial.month}

        header = tk.Frame(win)
        header.pack(fill="x", padx=fs(8), pady=fs(8))
        month_var = tk.StringVar(value="")
        tk.Button(header, text="◀", font=("Tahoma", fs(11), "bold"), width=3,
                  command=lambda: shift_month(-1)).pack(side="left")
        tk.Label(header, textvariable=month_var, font=("Tahoma", fs(12), "bold"), width=16).pack(side="left")
        tk.Button(header, text="▶", font=("Tahoma", fs(11), "bold"), width=3,
                  command=lambda: shift_month(1)).pack(side="left")

        days_frame = tk.Frame(win)
        days_frame.pack(padx=fs(8), pady=(0, fs(6)))

        def pick(day):
            target_var.set(date(state["year"], state["month"], day).strftime("%d/%m/%Y"))
            win.destroy()

        def render():
            month_var.set(f"{THAI_MONTH_NAMES[state['month'] - 1]} {state['year'] + 543}")
            for w in days_frame.winfo_children():
                w.destroy()
            for col, name in enumerate(["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]):
                tk.Label(days_frame, text=name, font=("Tahoma", fs(10), "bold"), width=4,
                         fg="#b91c1c" if col >= 5 else "black").grid(row=0, column=col)
            first = date(state["year"], state["month"], 1)
            start = first.weekday()
            if state["month"] == 12:
                nxt = date(state["year"] + 1, 1, 1)
            else:
                nxt = date(state["year"], state["month"] + 1, 1)
            days = (nxt - first).days
            r, c = 1, start
            for day in range(1, days + 1):
                tk.Button(days_frame, text=str(day), font=("Tahoma", fs(10)), width=4,
                          command=lambda d=day: pick(d)).grid(row=r, column=c, padx=1, pady=1)
                c += 1
                if c > 6:
                    c = 0
                    r += 1

        def shift_month(delta):
            m = state["month"] + delta
            y = state["year"]
            if m < 1:
                m, y = 12, y - 1
            elif m > 12:
                m, y = 1, y + 1
            state["month"], state["year"] = m, y
            render()

        render()
        tk.Button(win, text="วันนี้", font=("Tahoma", fs(10)),
                  command=lambda: pick(datetime.now().day) if (
                      state["year"] == datetime.now().year and state["month"] == datetime.now().month
                  ) else None).pack(pady=(0, fs(8)))
        win.lift()
        win.focus_force()

    def _build_date_field(self, parent, var):
        tk.Entry(parent, textvariable=var, width=12, font=("Tahoma", fs(12))).pack(
            side="left", padx=(fs(4), fs(2)))
        tk.Button(parent, text="📅", font=("Tahoma", fs(11)), width=2,
                  command=lambda: self.open_date_picker(parent, var)).pack(side="left", padx=(0, fs(10)))

    def _make_koryo_tree(self, parent, cols, headers, widths, height, center_cols=()):
        """Treeview with its own scrollbars - these ledgers grow without bound.
        Scrollbars claim their strip before the tree is packed; pack allocates
        in order and an expanding tree packed first leaves nothing after it."""
        wrap = tk.Frame(parent)
        tree = ttk.Treeview(wrap, columns=cols, show="headings", height=height, style="Koryo.Treeview")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=fs(widths[c]), anchor=("center" if c in center_cols else "w"))
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)
        return wrap, tree

    def _build_drug_filter(self, bar, on_change):
        """Both reports are filed one sheet per drug, so narrowing to a single
        drug before viewing/printing avoids paging through every drug."""
        tk.Label(bar, text="ยา", font=("Tahoma", fs(12))).pack(side="left", padx=(fs(6), 0))
        drug_var = tk.StringVar(value=ALL_DRUGS_LABEL)
        combo = ttk.Combobox(bar, textvariable=drug_var, values=[ALL_DRUGS_LABEL], state="readonly",
                             width=32, font=("Tahoma", fs(11)))
        combo.pack(side="left", padx=(fs(4), fs(10)))
        combo.bind("<<ComboboxSelected>>", lambda _e: on_change())
        return combo, drug_var

    def _sync_drug_filter(self, combo, drug_var, sheets):
        """Keep the current pick if that drug is still in range, so changing
        only the dates doesn't silently reset the filter."""
        names = [s["product_name"] for s in sheets]
        combo.config(values=[ALL_DRUGS_LABEL] + names)
        if drug_var.get() not in names:
            drug_var.set(ALL_DRUGS_LABEL)

    def _filter_sheets(self, sheets, drug_var):
        if drug_var.get() == ALL_DRUGS_LABEL:
            return sheets
        return [s for s in sheets if s["product_name"] == drug_var.get()]

    # -- the report screen ---------------------------------------------------

    def open_koryo_report_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("รายงาน ข.ย.9 / ข.ย.11")
        # Clamp to the screen: an over-tall window puts each tab's action
        # buttons below the desktop.
        win_h = min(fs(756), max(fs(420), win.winfo_screenheight() - fs(62)))
        win.geometry(f"{fs(1107)}x{win_h}")
        win.transient(self.root)

        style = ttk.Style(win)
        style.configure("Koryo.Treeview", font=("Tahoma", fs(12)), rowheight=fs(30))
        style.configure("Koryo.Treeview.Heading", font=("Tahoma", fs(11), "bold"))

        tab_bar = tk.Frame(win)
        tab_bar.pack(fill="x", padx=fs(8), pady=(fs(8), 0))
        body = tk.Frame(win)
        body.pack(fill="both", expand=True)

        tabs = [tk.Frame(body) for _ in range(3)]
        tab_buttons = []

        def show_tab(i):
            for t in tabs:
                t.pack_forget()
            tabs[i].pack(fill="both", expand=True)
            for j, b in enumerate(tab_buttons):
                b.config(bg="#1a5a9a" if j == i else "#dddddd", fg="white" if j == i else "black")

        for i, label in enumerate(["ข.ย.9 (บัญชีซื้อ)", "ข.ย.11 (ยาอันตราย)", "ข.ย.11 (tramadol)"]):
            b = tk.Button(tab_bar, text=label, font=("Tahoma", fs(11), "bold"),
                          command=lambda i=i: show_tab(i))
            b.pack(side="left", padx=(0, fs(4)))
            tab_buttons.append(b)

        self._build_ky9_tab(tabs[0])
        self._build_ky11_tab(tabs[1], "dangerous")
        self._build_ky11_tab(tabs[2], "tramadol")
        show_tab(0)
        win.lift()
        win.focus_force()

    def _build_ky9_tab(self, parent):
        pad = {"padx": fs(8), "pady": fs(4)}
        bar = tk.Frame(parent)
        bar.pack(fill="x", **pad)
        tk.Label(bar, text="จากวันที่", font=("Tahoma", fs(12))).pack(side="left")
        from_var = tk.StringVar(value="")
        self._build_date_field(bar, from_var)
        tk.Label(bar, text="ถึงวันที่", font=("Tahoma", fs(12))).pack(side="left")
        to_var = tk.StringVar(value="")
        self._build_date_field(bar, to_var)

        cols = ("seq", "date", "seller", "drug", "lot", "exp", "qty", "unit")
        headers = {"seq": "ลำดับ", "date": "วันที่ซื้อ", "seller": "ชื่อผู้ขาย", "drug": "ชื่อยา",
                   "lot": "Lot", "exp": "วันหมดอายุ", "qty": "จำนวน", "unit": "หน่วย"}
        widths = {"seq": 52, "date": 104, "seller": 170, "drug": 250, "lot": 110, "exp": 110,
                  "qty": 88, "unit": 78}
        tree_wrap, tree = self._make_koryo_tree(
            parent, cols, headers, widths, 16, ("seq", "exp", "qty", "unit"))
        tree_wrap.pack(fill="both", expand=True, padx=fs(8), pady=(0, fs(4)))

        state = {"sheets": []}

        def refresh_tree():
            for item in tree.get_children():
                tree.delete(item)
            for sheet in self._filter_sheets(state["sheets"], drug_var):
                for r in sheet["rows"]:
                    tree.insert("", "end", values=(
                        r["seq"], ky_date_dmy(r["date"]), r["seller"], sheet["product_name"],
                        r["lot_number"], ky_date_dmy(r.get("exp_date", "")), r["qty"], r["unit_name"],
                    ))

        def do_load():
            d_from = parse_date_flexible(from_var.get())
            d_to = parse_date_flexible(to_var.get())
            try:
                sheets = storage.build_ky9_sheets(
                    d_from.isoformat() if d_from else None, d_to.isoformat() if d_to else None)
            except Exception as e:
                messagebox.showerror("ผิดพลาด", f"โหลดรายงานไม่สำเร็จ: {e}", parent=parent)
                return
            state["sheets"] = sheets
            self._sync_drug_filter(drug_combo, drug_var, sheets)
            refresh_tree()

        def do_print():
            sheets = self._filter_sheets(state["sheets"], drug_var)
            if not sheets:
                messagebox.showinfo("แจ้งเตือน", "ยังไม่มีข้อมูล - กด \"แสดงรายงาน\" ก่อน", parent=parent)
                return
            open_html_report(
                render_ky9_print_html(sheets, ky_date_label(from_var.get(), to_var.get())),
                "koryo9_report")

        drug_combo, drug_var = self._build_drug_filter(bar, refresh_tree)
        tk.Button(bar, text="แสดงรายงาน", font=("Tahoma", fs(12), "bold"), bg="#1a5a9a", fg="white",
                  command=do_load).pack(side="left", padx=(0, fs(6)))
        tk.Button(bar, text="🖨️ พิมพ์", font=("Tahoma", fs(12), "bold"),
                  command=do_print).pack(side="left")
        do_load()

    def _build_ky11_tab(self, parent, category):
        pad = {"padx": fs(8), "pady": fs(4)}
        bar = tk.Frame(parent)
        bar.pack(fill="x", **pad)
        tk.Label(bar, text="จากวันที่", font=("Tahoma", fs(12))).pack(side="left")
        from_var = tk.StringVar(value="")
        self._build_date_field(bar, from_var)
        tk.Label(bar, text="ถึงวันที่", font=("Tahoma", fs(12))).pack(side="left")
        to_var = tk.StringVar(value="")
        self._build_date_field(bar, to_var)
        drug_combo, drug_var = self._build_drug_filter(bar, lambda: refresh_tree())
        tk.Button(bar, text="แสดงรายงาน", font=("Tahoma", fs(12), "bold"), bg="#1a5a9a", fg="white",
                  command=lambda: do_load()).pack(side="left", padx=(0, fs(6)))
        tk.Button(bar, text="🖨️ พิมพ์", font=("Tahoma", fs(12), "bold"),
                  command=lambda: do_print()).pack(side="left")

        hint = (
            "แถวสีชมพู = ยังไม่ได้กรอกเลขบัตร/ที่อยู่ - ดับเบิลคลิกเพื่อกรอกภายหลัง หรือแก้วันที่ขาย/ชื่อผู้ซื้อ"
            if category == "tramadol" else
            "ดับเบิลคลิกที่แถว เพื่อแก้วันที่ขาย/ชื่อผู้ซื้อ/จำนวน (สำหรับคีย์เอกสารย้อนหลัง)"
        )
        tk.Label(parent, text=hint, font=("Tahoma", fs(10)),
                 fg=("#b91c1c" if category == "tramadol" else "#444")).pack(anchor="w", padx=fs(8))

        cols = ("drug", "date", "qty", "unit", "lot", "remain", "buyer", "status")
        headers = {"drug": "ชื่อยา", "date": "วันที่ขาย", "qty": "จำนวน", "unit": "หน่วย", "lot": "Lot",
                   "remain": "คงเหลือใน Lot", "buyer": "ผู้ซื้อ", "status": "สถานะ"}
        widths = {"drug": 210, "date": 104, "qty": 78, "unit": 78, "lot": 110, "remain": 110,
                  "buyer": 260, "status": 100}
        tree_wrap, tree = self._make_koryo_tree(
            parent, cols, headers, widths, 15, ("qty", "unit", "status", "remain"))
        tree.tag_configure("incomplete", background="#fde7e7")
        # Action row packed before the tree, against the bottom - otherwise the
        # expanding tree takes the whole tab and these buttons never appear.
        act = tk.Frame(parent)
        act.pack(side="bottom", fill="x", padx=fs(8), pady=(0, fs(6)))
        tree_wrap.pack(fill="both", expand=True, padx=fs(8), pady=(0, fs(4)))

        state = {"sheets": []}
        row_index = {}

        def do_load():
            d_from = parse_date_flexible(from_var.get())
            d_to = parse_date_flexible(to_var.get())
            try:
                sheets = storage.build_ky11_sheets(
                    category, d_from.isoformat() if d_from else None,
                    d_to.isoformat() if d_to else None)
            except Exception as e:
                messagebox.showerror("ผิดพลาด", f"โหลดรายงานไม่สำเร็จ: {e}", parent=parent)
                return
            state["sheets"] = sheets
            self._sync_drug_filter(drug_combo, drug_var, sheets)
            refresh_tree()

        def refresh_tree():
            for item in tree.get_children():
                tree.delete(item)
            row_index.clear()
            for sheet in self._filter_sheets(state["sheets"], drug_var):
                for s in sheet["sales"]:
                    if category == "tramadol":
                        buyer_display = s["buyer_name"] + (
                            f" · {s['citizen_id']}" if s["citizen_id"] else " · (ยังไม่มีเลขบัตร)")
                        status = "ครบแล้ว" if s["info_complete"] else "ยังไม่ครบ"
                    else:
                        buyer_display = s["buyer_name"]
                        status = ""
                    # "-" = the lot this sale came from has since been deleted
                    rem = s.get("lot_remaining")
                    iid = tree.insert("", "end", values=(
                        sheet["product_name"], ky_date_dmy(s["date"]), f"{float(s['qty']):g}",
                        sheet["unit_name"], s["lot_number"],
                        (f"{rem:g}" if rem is not None else "-"), buyer_display, status,
                    ))
                    if category == "tramadol" and not s["info_complete"]:
                        tree.item(iid, tags=("incomplete",))
                    row_index[iid] = s

        def do_print():
            sheets = self._filter_sheets(state["sheets"], drug_var)
            if not sheets:
                messagebox.showinfo("แจ้งเตือน", "ยังไม่มีข้อมูล - กด \"แสดงรายงาน\" ก่อน", parent=parent)
                return
            open_html_report(
                render_ky11_print_html(sheets, category,
                                       ky_date_label(from_var.get(), to_var.get())),
                f"koryo11_{category}_report")

        def on_double_click(_event=None):
            sel = tree.selection()
            s = row_index.get(sel[0]) if sel else None
            if s:
                self._open_edit_controlled_sale_dialog(parent, s, category, do_load)

        cat_label = "tramadol" if category == "tramadol" else "ยาอันตราย"

        def do_delete_row():
            sel = tree.selection()
            s = row_index.get(sel[0]) if sel else None
            if not s:
                messagebox.showinfo("แจ้งเตือน", "เลือกแถวที่จะลบก่อน", parent=parent)
                return
            back = (f"\n\nจำนวน {float(s['qty']):g} จะถูกคืนเข้า Lot {s['lot_number']}"
                    if s.get("lot_id") else "")
            if not messagebox.askyesno(
                "ยืนยันลบรายการขาย",
                f"ลบรายการขายนี้ออกจากบัญชี ข.ย.11 ?\n\n"
                f"{ky_date_dmy(s['date'])} - {s.get('buyer_name') or '(ไม่มีชื่อ)'} - "
                f"{float(s['qty']):g}{back}",
                parent=parent,
            ):
                return
            try:
                storage.delete_controlled_sale(s["id"])
            except Exception as e:
                messagebox.showerror("ผิดพลาด", f"ลบไม่สำเร็จ: {e}", parent=parent)
                return
            do_load()

        def do_clear_all():
            # Two prompts on purpose: this wipes a legally-required ledger, and
            # the second spells out what is and isn't touched.
            if not messagebox.askyesno(
                "ล้างประวัติ ข.ย.11",
                f"ล้างรายการขาย ข.ย.11 ({cat_label}) ทั้งหมดในฐานข้อมูล?\n\n"
                "ใช้สำหรับล้างข้อมูลที่ทดสอบไว้\n"
                "(ล้างทุกตัวยา ทุกช่วงวันที่ ไม่สนใจตัวกรองบนหน้าจอ)",
                parent=parent,
            ):
                return
            if not messagebox.askyesno(
                "ยืนยันอีกครั้ง",
                f"ยืนยันล้างบัญชีขาย ข.ย.11 ({cat_label}) ทั้งหมด?\n\n"
                "• จำนวนที่ขายไปจะถูกคืนเข้า Lot ให้อัตโนมัติ\n"
                "• บัญชีซื้อ ข.ย.9 (รายการ Lot) ไม่ถูกลบ\n"
                f"• อีกแท็บ ({'ยาอันตราย' if category == 'tramadol' else 'tramadol'}) ไม่ถูกแตะ\n\n"
                "ย้อนกลับไม่ได้",
                parent=parent,
            ):
                return
            try:
                removed = storage.clear_controlled_sales(category=category)
            except Exception as e:
                messagebox.showerror("ผิดพลาด", f"ล้างไม่สำเร็จ: {e}", parent=parent)
                return
            do_load()
            messagebox.showinfo("ล้างแล้ว",
                                f"ลบรายการขาย ข.ย.11 ({cat_label}) ไป {removed} รายการ", parent=parent)

        tk.Button(act, text="✏ แก้ไข/แก้จำนวน", font=("Tahoma", fs(10), "bold"),
                  command=on_double_click).pack(side="left")
        tk.Button(act, text="🗑 ลบแถวที่เลือก", font=("Tahoma", fs(10), "bold"), bg="#b45309",
                  fg="white", command=do_delete_row).pack(side="left", padx=(fs(6), 0))
        tk.Button(act, text=f"🧹 ล้างประวัติ ข.ย.11 ({cat_label}) ทั้งหมด",
                  font=("Tahoma", fs(10), "bold"), bg="#b91c1c", fg="white",
                  command=do_clear_all).pack(side="right")

        tree.bind("<Double-Button-1>", on_double_click)
        do_load()

    # -- correcting a recorded sale -----------------------------------------

    def _open_edit_controlled_sale_dialog(self, parent, sale, category, on_saved):
        """Always offers วันที่ขาย / ชื่อผู้ซื้อ / จำนวน - rows are stamped at
        print time and staff key things wrong. For tramadol it also fills in
        the citizen ID + address for a sale printed before that was on hand."""
        is_tramadol = category == "tramadol"
        win = tk.Toplevel(parent)
        win.title(f"แก้ไขรายการขาย - {sale.get('buyer_name') or ''}")
        win.geometry(f"{fs(520)}x{fs(590)}" if is_tramadol else f"{fs(520)}x{fs(360)}")
        win.transient(parent)
        win.grab_set()

        pad = {"padx": fs(10), "pady": fs(4)}
        tk.Label(win, text="วันที่ขาย", font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
        date_row = tk.Frame(win)
        date_row.pack(anchor="w", padx=fs(10))
        date_var = tk.StringVar(value=format_date_dmy(parse_date_flexible(str(sale.get("date") or "")[:10])))
        self._build_date_field(date_row, date_var)

        rem = sale.get("lot_remaining")
        rem_note = f"   (Lot {sale.get('lot_number')} เหลือ {rem:g})" if rem is not None else ""
        tk.Label(win, text="จำนวนที่จ่าย" + rem_note,
                 font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
        qty_var = tk.StringVar(value=f"{float(sale.get('qty') or 0):g}")
        qty_row = tk.Frame(win)
        qty_row.pack(anchor="w", padx=fs(10))
        tk.Entry(qty_row, textvariable=qty_var, font=("Tahoma", fs(12)), width=10).pack(side="left")
        tk.Label(qty_row, text="แก้แล้วจำนวนใน Lot จะปรับตามให้อัตโนมัติ",
                 font=("Tahoma", fs(9)), fg="#555").pack(side="left", padx=(fs(8), 0))

        tk.Label(win, text="ชื่อ-สกุลผู้ซื้อ", font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
        name_var = tk.StringVar(value=sale.get("buyer_name", "") or "")
        tk.Entry(win, textvariable=name_var, font=("Tahoma", fs(12))).pack(fill="x", **pad)

        id_var = tk.StringVar(value=sale.get("citizen_id", ""))
        addr_text = None
        if is_tramadol:
            tk.Label(win, text="เลขบัตรประชาชน", font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
            tk.Entry(win, textvariable=id_var, font=("Tahoma", fs(12))).pack(fill="x", **pad)
            tk.Label(win, text="ที่อยู่ตามบัตร", font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
            addr_text = tk.Text(win, font=("Tahoma", fs(12)), height=4)
            addr_text.insert("1.0", sale.get("address", ""))
            addr_text.pack(fill="both", expand=True, padx=fs(10))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, font=("Tahoma", fs(11)), fg="#b91c1c").pack(
            anchor="w", padx=fs(10))

        def on_save():
            new_date = parse_date_flexible(date_var.get())
            if not new_date:
                status_var.set("วันที่ขายไม่ถูกต้อง")
                return
            buyer_name = name_var.get().strip()
            if not buyer_name:
                status_var.set("กรุณากรอกชื่อผู้ซื้อ")
                return
            try:
                new_qty = float(qty_var.get().strip())
            except ValueError:
                status_var.set("จำนวนที่จ่ายต้องเป็นตัวเลข")
                return
            if new_qty <= 0:
                status_var.set("จำนวนที่จ่ายต้องมากกว่า 0")
                return
            citizen_id = id_var.get().strip()
            address = addr_text.get("1.0", "end").strip() if addr_text else ""
            if is_tramadol and (not citizen_id or not address):
                status_var.set("กรุณากรอกทั้งเลขบัตรและที่อยู่")
                return
            try:
                if abs(new_qty - float(sale.get("qty") or 0)) > 1e-9:
                    storage.adjust_controlled_sale_qty(sale["id"], new_qty)
                storage.update_controlled_sale_basics(sale["id"], new_date.isoformat(), buyer_name)
                if is_tramadol:
                    storage.fill_controlled_sale_info(sale["id"], citizen_id, address)
            except Exception as e:
                status_var.set(f"บันทึกไม่สำเร็จ: {e}")
                return
            win.destroy()
            on_saved()

        btn_row = tk.Frame(win)
        btn_row.pack(pady=fs(8))
        tk.Button(btn_row, text="💾 บันทึก", font=("Tahoma", fs(12), "bold"), bg="#1a7a4a",
                  fg="white", command=on_save).pack(side="left", padx=fs(6))
        tk.Button(btn_row, text="ยกเลิก", font=("Tahoma", fs(12)),
                  command=win.destroy).pack(side="left", padx=fs(6))
        win.lift()
        win.focus_force()

    # -- tramadol buyer confirmation at print time --------------------------

    def _ask_tramadol_buyer_info(self, parent, buyer_name, prior):
        """Confirm the buyer's citizen ID/address before a tramadol sale is
        written. Previous details are pre-filled for the pharmacist to eyeball
        rather than written silently. Returns the dict to record, or None for
        ข้าม (row stays flagged, filled in later from the report screen)."""
        win = tk.Toplevel(parent)
        win.title("ข้อมูลผู้ซื้อ (tramadol)")
        win.geometry(f"{fs(500)}x{fs(430)}")
        win.transient(parent)
        win.grab_set()

        pad = {"padx": fs(12), "pady": fs(4)}
        tk.Label(win, text=f"ผู้ซื้อ: {buyer_name}", font=("Tahoma", fs(13), "bold")).pack(
            anchor="w", padx=fs(12), pady=(fs(10), 0))
        if prior and prior.get("matched_by") == "name":
            banner = ("พบข้อมูลเดิมจากชื่อที่ตรงกัน (ยังไม่ได้ผูกแฟ้มลูกค้า)\n"
                      "กรุณาตรวจสอบให้แน่ใจว่าเป็นคนเดียวกัน ก่อนกดตกลง")
            banner_fg = "#b45309"
        elif prior:
            banner, banner_fg = "พบข้อมูลเดิมของลูกค้ารายนี้ - กรุณาตรวจสอบว่าถูกคน แล้วกดตกลง", "#1a7a4a"
        else:
            banner, banner_fg = "ไม่พบข้อมูลเดิม - กรอกเลย หรือกดข้ามเพื่อกรอกทีหลัง", "#b45309"
        tk.Label(win, text=banner, font=("Tahoma", fs(11)), fg=banner_fg,
                 wraplength=fs(470), justify="left").pack(anchor="w", **pad)

        tk.Label(win, text="เลขบัตรประชาชน", font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
        id_var = tk.StringVar(value=(prior or {}).get("citizen_id", ""))
        id_entry = tk.Entry(win, textvariable=id_var, font=("Tahoma", fs(12)))
        id_entry.pack(fill="x", **pad)

        tk.Label(win, text="ที่อยู่ตามบัตร", font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)
        addr_text = tk.Text(win, font=("Tahoma", fs(12)), height=4)
        addr_text.insert("1.0", (prior or {}).get("address", ""))
        addr_text.pack(fill="both", expand=True, padx=fs(12))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, font=("Tahoma", fs(11)), fg="#b91c1c").pack(
            anchor="w", padx=fs(12))

        result = {"info": None}

        def on_ok():
            citizen_id = id_var.get().strip()
            address = addr_text.get("1.0", "end").strip()
            if not citizen_id or not address:
                status_var.set("กรุณากรอกทั้งเลขบัตรและที่อยู่ หรือกด \"ข้าม (กรอกทีหลัง)\"")
                return
            result["info"] = {"citizen_id": citizen_id, "address": address}
            win.destroy()

        btn_row = tk.Frame(win)
        btn_row.pack(pady=fs(10))
        tk.Button(btn_row, text="✓ ตกลง", font=("Tahoma", fs(12), "bold"), bg="#1a7a4a",
                  fg="white", command=on_ok).pack(side="left", padx=fs(6))
        tk.Button(btn_row, text="ข้าม (กรอกทีหลัง)", font=("Tahoma", fs(12)),
                  command=win.destroy).pack(side="left", padx=fs(6))

        win.lift()
        win.focus_force()
        id_entry.focus_set()
        parent.wait_window(win)
        return result["info"]

    # -- shared by พิมพ์ฉลาก and บันทึกประวัติ --------------------------------

    def controlled_precheck(self, parent, name):
        """A controlled drug leaving the shelf has to reach ข.ย.11 whether or
        not a sticker is printed, so both paths demand a real buyer name, ask
        the quantity, and ask for the tramadol details up front.

        Returns (controlled, tramadol_info, ok) where controlled is a list of
        (drug, category, qty). ok=False means the caller must abort."""
        controlled = []
        for dd in self.selected_drugs:
            try:
                cat = storage.get_drug_report_category(dd.get("idproduct"))
            except Exception:
                cat = "none"
            if cat in ("dangerous", "tramadol"):
                controlled.append((dd, cat))
        if not controlled:
            return [], None, True
        if not name or name == "ไม่ประสงค์ออกนาม":
            names = ", ".join(sorted({dd.get("drug1", "") for dd, _ in controlled}))
            messagebox.showwarning(
                "ต้องกรอกชื่อผู้ซื้อ",
                f"รายการนี้มียาที่ต้องรายงาน ข.ย.11 ({names})\n"
                "กรุณากรอกชื่อ-นามสกุลผู้ซื้อก่อน "
                "(ใช้ \"ไม่ประสงค์ออกนาม\" แทนไม่ได้)",
                parent=parent,
            )
            return controlled, None, False
        # This build has no dispense-quantity field on the drug row (HOPE gets
        # it from its price tiers), and the label count is not the amount
        # handed over, so ask rather than guess - a wrong number here is both
        # a wrong ledger entry and a wrong stock figure.
        with_qty = self._ask_controlled_quantities(parent, controlled)
        if with_qty is None:
            return controlled, None, False
        tramadol_info = None
        if any(cat == "tramadol" for _, cat, _, _ in with_qty):
            try:
                prior = storage.get_last_tramadol_buyer_info(
                    getattr(self, "_queue_patient_id", None), name)
            except Exception:
                prior = None
            tramadol_info = self._ask_tramadol_buyer_info(parent, name, prior)
        return with_qty, tramadol_info, True

    def _lot_unit_for(self, template_id, fallback=""):
        """The unit a drug's stock is counted in - taken from its most recent
        ข.ย.9 lot, because that is the unit the pharmacist actually typed when
        recording it. Deliberately NOT the drug's `unit` field: that is the
        dosing unit ("ทานครั้งละ 1 เม็ด"), and counting a sale in เม็ด against
        a lot received in แผง would subtract one unit from another. HOPE
        converts via its price tiers; this build has none, so it keeps
        everything in the lot's own unit."""
        try:
            lots = storage.get_purchase_lots(template_id) if template_id else []
        except Exception:
            lots = []
        for lt in reversed(lots):
            if lt.get("unit_name"):
                return lt["unit_name"]
        return fallback

    def _ask_controlled_quantities(self, parent, controlled):
        """One row per controlled drug: how much was actually dispensed, in
        that drug's stock unit. Returns [(drug, category, qty, unit)] or None
        if cancelled."""
        win = tk.Toplevel(parent)
        win.title("จำนวนที่จ่าย (ข.ย.11)")
        win.geometry(f"{fs(520)}x{fs(160 + 46 * len(controlled))}")
        win.transient(parent)
        win.grab_set()

        tk.Label(win, text="ยาที่ต้องรายงาน ข.ย.11 - กรอกจำนวนที่จ่ายจริง",
                 font=("Tahoma", fs(12), "bold")).pack(anchor="w", padx=fs(12), pady=(fs(10), fs(2)))
        tk.Label(win, text="จำนวนนี้จะถูกบันทึกลงบัญชีและตัดออกจาก Lot",
                 font=("Tahoma", fs(9)), fg="#555").pack(anchor="w", padx=fs(12))

        vars_ = []
        for dd, cat in controlled:
            row = tk.Frame(win)
            row.pack(fill="x", padx=fs(12), pady=fs(3))
            tag = "tramadol" if cat == "tramadol" else "ยาอันตราย"
            tk.Label(row, text=f"{dd.get('drug1', '')}  ({tag})", font=("Tahoma", fs(10)),
                     anchor="w", wraplength=fs(280), justify="left").pack(side="left", fill="x", expand=True)
            v = tk.StringVar(value="1")
            tk.Entry(row, textvariable=v, font=("Tahoma", fs(11)), width=8).pack(side="left", padx=fs(6))
            unit = self._lot_unit_for(dd.get("idproduct"))
            tk.Label(row, text=(unit or "(ยังไม่มีล็อต)"), font=("Tahoma", fs(10)),
                     fg=("black" if unit else "#b45309")).pack(side="left")
            vars_.append((dd, cat, v, unit))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, font=("Tahoma", fs(10)), fg="#b91c1c").pack(
            anchor="w", padx=fs(12))

        result = {"rows": None}

        def on_ok():
            out = []
            for dd, cat, v, unit in vars_:
                try:
                    q = float(v.get().strip())
                except ValueError:
                    status_var.set(f"จำนวนของ {dd.get('drug1', '')} ต้องเป็นตัวเลข")
                    return
                if q <= 0:
                    status_var.set(f"จำนวนของ {dd.get('drug1', '')} ต้องมากกว่า 0")
                    return
                out.append((dd, cat, q, unit))
            result["rows"] = out
            win.destroy()

        btn_row = tk.Frame(win)
        btn_row.pack(pady=fs(10))
        tk.Button(btn_row, text="✓ ตกลง", font=("Tahoma", fs(12), "bold"), bg="#1a7a4a",
                  fg="white", command=on_ok).pack(side="left", padx=fs(6))
        tk.Button(btn_row, text="ยกเลิก", font=("Tahoma", fs(12)),
                  command=win.destroy).pack(side="left", padx=fs(6))
        win.lift()
        win.focus_force()
        parent.wait_window(win)
        return result["rows"]

    def record_controlled_sales(self, controlled, name, phone, patient_id,
                                tramadol_info, print_job_id):
        """Write the ข.ย.11 rows and draw the quantities out of their lots.
        Best-effort per drug: whatever already happened must never be rolled
        back over a ledger write. Returns the patient_id actually used, which
        may have been created here."""
        if not controlled:
            return patient_id
        if not patient_id:
            try:
                patient_id = self._ensure_patient_for_controlled_sale(name, phone)
            except Exception:
                patient_id = None
        for dd, cat, qty, unit in controlled:
            try:
                lot_id, lot_number = storage.fifo_decrement_lot(dd.get("idproduct"), qty)
                carried = tramadol_info if cat == "tramadol" else None
                storage.save_controlled_sale(
                    dd.get("idproduct"), dd.get("drug1", ""), lot_id, lot_number, qty,
                    unit, cat, name,
                    buyer_citizen_id=(carried or {}).get("citizen_id", ""),
                    buyer_address=(carried or {}).get("address", ""),
                    print_job_id=print_job_id, patient_id=patient_id,
                )
            except Exception:
                pass
        return patient_id

    def _ensure_patient_for_controlled_sale(self, name, phone):
        """Link a controlled-drug buyer to a patients record so the next sale
        can match on patient_id instead of the spelling of their name. Both
        categories require a real buyer name, so both get linked.

        Returns None rather than guessing when the name is ambiguous: two
        people sharing a name are told apart only by phone, and picking one
        would attach a citizen ID to possibly the wrong person."""
        name = (name or "").strip()
        if not name:
            return None
        if (phone or "").strip():
            return storage.find_or_create_patient(name, phone)
        try:
            existing = storage.find_patients_by_exact_name(name)
        except Exception:
            return None
        if len(existing) == 1:
            return existing[0]["id"]
        if not existing:
            return storage.find_or_create_patient(name, "")
        return None

    # -- ⚙ per-drug ข.ย. settings -------------------------------------------

    def open_drug_report_dialog(self, index):
        """⚙ popup - set this drug's ข.ย. reporting category and jump to lot
        entry (ข.ย.9). Kept out of the drug edit dialog, which is crowded
        enough that adding rows there would force a scrollbar."""
        d = self.selected_drugs[index]
        win = tk.Toplevel(self.root)
        win.title(f"ยาควบคุม (ข.ย.) - {d.get('drug1', '')}")
        win.geometry(f"{fs(480)}x{fs(270)}")
        win.transient(self.root)
        win.grab_set()

        pad = {"padx": fs(12), "pady": fs(6)}
        tk.Label(win, text=d.get("drug1", ""), font=("Tahoma", fs(11), "bold"),
                 wraplength=fs(440), justify="left").pack(anchor="w", **pad)

        labels = {key: lbl for key, lbl in DRUG_REPORT_CATEGORIES}
        label_to_key = {lbl: key for key, lbl in DRUG_REPORT_CATEGORIES}
        current = d.get("drug_report_category") or "none"
        if d.get("idproduct"):
            try:
                current = storage.get_drug_report_category(d["idproduct"])
            except Exception:
                pass

        tk.Label(win, text="การรายงานยาควบคุม (ข.ย.)",
                 font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
        display_var = tk.StringVar(value=labels.get(current, labels["none"]))
        ttk.Combobox(win, textvariable=display_var, values=list(labels.values()),
                     state="readonly", font=("Tahoma", fs(10))).pack(fill="x", padx=fs(12))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, font=("Tahoma", fs(9)), fg="#1a7a4a",
                 wraplength=fs(440), justify="left").pack(anchor="w", padx=fs(12), pady=(fs(4), 0))

        lot_btn = tk.Button(
            win, text="📦 บันทึกล็อตใหม่ (ข.ย.9)", font=("Tahoma", fs(10), "bold"),
            bg="#7a4a1a", fg="white",
            command=lambda: self.open_purchase_lot_dialog(win, d.get("idproduct"), d.get("drug1", "")),
        )
        lot_btn.pack(**pad)

        def sync(*_a):
            key = label_to_key.get(display_var.get(), "none")
            enabled = key != "none" and bool(d.get("idproduct"))
            lot_btn.config(state="normal" if enabled else "disabled")
            if key != "none" and not d.get("idproduct"):
                status_var.set("บันทึกยานี้ลงฐานข้อมูลก่อน จึงจะบันทึกล็อตได้")
            else:
                status_var.set("")

        display_var.trace_add("write", sync)
        sync()

        def on_save():
            key = label_to_key.get(display_var.get(), "none")
            d["drug_report_category"] = key
            if d.get("idproduct"):
                try:
                    storage.set_drug_report_category(d["idproduct"], key)
                except Exception as e:
                    status_var.set(f"บันทึกไม่สำเร็จ: {e}")
                    return
            self.refresh_selected_list()
            win.destroy()

        btn_row = tk.Frame(win)
        btn_row.pack(pady=fs(8))
        tk.Button(btn_row, text="💾 บันทึก", font=("Tahoma", fs(11), "bold"), bg="#1a7a4a",
                  fg="white", command=on_save).pack(side="left", padx=fs(4))
        tk.Button(btn_row, text="ปิด", font=("Tahoma", fs(11)),
                  command=win.destroy).pack(side="left", padx=fs(4))
        win.lift()
        win.focus_force()

    # -- ข.ย.9 lot entry -----------------------------------------------------

    def open_purchase_lot_dialog(self, parent_win, template_id, drug_name):
        """Record lots received for one drug (ข.ย.9), and show what's already
        on file with how much is left."""
        win = tk.Toplevel(parent_win)
        win.title(f"บันทึกการซื้อ (ข.ย.9) - {drug_name}")
        win.geometry(f"{fs(560)}x{fs(560)}")
        win.transient(parent_win)
        win.grab_set()

        pad = {"padx": fs(10), "pady": fs(3)}
        tk.Label(win, text=drug_name, font=("Tahoma", fs(12), "bold")).pack(anchor="w", **pad)

        tk.Label(win, text="วันที่รับเข้า", font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
        date_row = tk.Frame(win)
        date_row.pack(anchor="w", padx=fs(10))
        date_var = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        self._build_date_field(date_row, date_var)

        tk.Label(win, text="ซื้อจาก (ชื่อผู้ขาย)", font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
        source_var = tk.StringVar(value="")
        tk.Entry(win, textvariable=source_var, font=("Tahoma", fs(11))).pack(fill="x", **pad)

        tk.Label(win, text="Lot", font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
        lot_var = tk.StringVar(value="")
        tk.Entry(win, textvariable=lot_var, font=("Tahoma", fs(11))).pack(fill="x", **pad)

        tk.Label(win, text="วันหมดอายุ", font=("Tahoma", fs(10), "bold")).pack(anchor="w", **pad)
        exp_row = tk.Frame(win)
        exp_row.pack(anchor="w", padx=fs(10))
        exp_var = tk.StringVar(value="")
        self._build_date_field(exp_row, exp_var)

        qty_row = tk.Frame(win)
        qty_row.pack(anchor="w", **pad)
        tk.Label(qty_row, text="จำนวนที่รับ", font=("Tahoma", fs(10), "bold")).pack(side="left")
        qty_var = tk.StringVar(value="")
        tk.Entry(qty_row, textvariable=qty_var, font=("Tahoma", fs(11)), width=10).pack(
            side="left", padx=fs(6))
        tk.Label(qty_row, text="หน่วย", font=("Tahoma", fs(10), "bold")).pack(side="left")
        unit_var = tk.StringVar(value="")
        tk.Entry(qty_row, textvariable=unit_var, font=("Tahoma", fs(11)), width=12).pack(
            side="left", padx=fs(6))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, font=("Tahoma", fs(10)), fg="#b91c1c").pack(
            anchor="w", padx=fs(10))

        tk.Label(win, text="ล็อตที่บันทึกไว้แล้ว", font=("Tahoma", fs(10), "bold")).pack(
            anchor="w", **pad)
        list_wrap = tk.Frame(win)
        list_wrap.pack(fill="both", expand=True, padx=fs(10), pady=(0, fs(4)))
        lots_list = tk.Listbox(list_wrap, font=("Tahoma", fs(9)), height=6)
        lots_sb = ttk.Scrollbar(list_wrap, orient="vertical", command=lots_list.yview)
        lots_list.configure(yscrollcommand=lots_sb.set)
        lots_sb.pack(side="right", fill="y")
        lots_list.pack(side="left", fill="both", expand=True)
        lot_ids = []

        def refresh_lots():
            lots_list.delete(0, tk.END)
            lot_ids.clear()
            try:
                lots = storage.get_purchase_lots(template_id)
            except Exception as e:
                lots_list.insert(tk.END, f"โหลดไม่สำเร็จ: {e}")
                return
            if not lots:
                lots_list.insert(tk.END, "(ยังไม่มีล็อตที่บันทึกไว้)")
                return
            for lt in lots:
                exp_part = f" · หมดอายุ {ky_date_dmy(lt['exp_date'])}" if lt.get("exp_date") else ""
                lots_list.insert(tk.END, (
                    f"{ky_date_dmy(lt['received_date'])} · {lt['source_company'] or '-'} · "
                    f"Lot {lt['lot_number'] or '-'} · เหลือ {lt['qty_remaining']:g}/"
                    f"{lt['qty_received']:g} {lt['unit_name']}{exp_part}"))
                lot_ids.append(lt["id"])

        def on_save():
            received = parse_date_flexible(date_var.get())
            if not received:
                status_var.set("กรุณาใส่วันที่รับเข้าให้ถูกต้อง")
                return
            try:
                qty = float(qty_var.get().strip())
            except ValueError:
                status_var.set("กรุณาใส่จำนวนเป็นตัวเลข")
                return
            if qty <= 0:
                status_var.set("จำนวนต้องมากกว่า 0")
                return
            exp = parse_date_flexible(exp_var.get())
            try:
                storage.save_purchase_lot(
                    template_id, drug_name, received.isoformat(), source_var.get().strip(),
                    lot_var.get().strip(), qty, unit_var.get().strip(),
                    exp.isoformat() if exp else "")
            except Exception as e:
                status_var.set(f"บันทึกไม่สำเร็จ: {e}")
                return
            status_var.set("")
            qty_var.set("")
            lot_var.set("")
            exp_var.set("")
            refresh_lots()

        def on_delete():
            sel = lots_list.curselection()
            if not sel or sel[0] >= len(lot_ids):
                return
            if not messagebox.askyesno("ยืนยันลบ", "ลบล็อตนี้?", parent=win):
                return
            try:
                storage.delete_purchase_lot(lot_ids[sel[0]])
            except Exception as e:
                messagebox.showerror("ผิดพลาด", f"ลบไม่สำเร็จ: {e}", parent=win)
                return
            refresh_lots()

        btn_row = tk.Frame(win)
        btn_row.pack(pady=fs(8))
        tk.Button(btn_row, text="💾 บันทึกล็อตนี้", font=("Tahoma", fs(11), "bold"), bg="#1a7a4a",
                  fg="white", command=on_save).pack(side="left", padx=fs(4))
        tk.Button(btn_row, text="🗑 ลบล็อตที่เลือก", font=("Tahoma", fs(11)),
                  command=on_delete).pack(side="left", padx=fs(4))
        tk.Button(btn_row, text="ปิด", font=("Tahoma", fs(11)),
                  command=win.destroy).pack(side="left", padx=fs(4))

        refresh_lots()
        win.lift()
        win.focus_force()
