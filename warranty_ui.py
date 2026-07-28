"""Desktop warranty UI for LabelPrinterStandalone_MobileQueue.

Mirrors HOPE label_printer warranty dialogs (list + single-page add form +
import/clear) but talks to local SQLite via storage.py — no SQL Server.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import tkinter as tk
from tkinter import messagebox, filedialog, ttk

import storage

# Avoid circular import with label_gui — local copies of SCRIPT_DIR / fs scale.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def fs(n):
    """Match label_gui.fs for dialog sizing (plain scale; LabelApp may override UI density)."""
    try:
        # Prefer live scale from label_gui if already loaded (no cycle at import time).
        import label_gui as _lg
        if hasattr(_lg, "fs"):
            return _lg.fs(n)
    except Exception:
        pass
    return max(1, int(round(n)))


class WarrantyMixin:
    """Mixin for LabelApp — open_warranty_dialog / form / mobile URL / clear."""

    def open_warranty_mobile_info_dialog(self):
        """URL หน้ามือถือประกัน บน server LAN เดียวกับคิวพิมพ์."""
        win = tk.Toplevel(self.root)
        win.title("ประกันอุปกรณ์จากมือถือ")
        win.geometry(f"{fs(460)}x{fs(240)}")
        win.transient(self.root)
        win.grab_set()
        if not self.queue_url:
            tk.Label(
                win, text="เปิด server สำหรับมือถือไม่สำเร็จ\nลองปิดโปรแกรมแล้วเปิดใหม่",
                font=("Tahoma", fs(11)), fg="#b03a2e", justify="left",
            ).pack(padx=fs(16), pady=fs(20))
            tk.Button(win, text="ปิด", font=("Tahoma", fs(10)), command=win.destroy).pack(pady=fs(8))
            return
        url = self.queue_url.rstrip("/") + "/warranty"
        tk.Label(
            win, text="เปิดในมือถือ (WiFi ร้านเดียวกับเครื่องนี้) — หน้าเดียว:\n"
                      "ค้นหาลูกค้า → เลือก/สร้าง → ค้นหาอุปกรณ์ → บันทึกประกัน",
            font=("Tahoma", fs(9), "bold"), fg="#6a4a1a", wraplength=fs(420), justify="left",
        ).pack(anchor="w", padx=fs(12), pady=(fs(12), fs(6)))
        url_row = tk.Frame(win)
        url_row.pack(fill="x", padx=fs(12), pady=(0, fs(4)))
        url_var = tk.StringVar(value=url)
        tk.Entry(
            url_row, textvariable=url_var, font=("Tahoma", fs(11), "bold"), fg="#5a5a9a",
            state="readonly", readonlybackground="white", relief="solid", bd=1,
        ).pack(side="left", fill="x", expand=True, ipady=fs(3))
        copied_var = tk.StringVar(value="")

        def _copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            copied_var.set(f"คัดลอก {url} แล้ว")

        tk.Button(url_row, text="📋 คัดลอก", font=("Tahoma", fs(9)), command=_copy).pack(
            side="left", padx=(fs(4), 0)
        )
        tk.Label(win, textvariable=copied_var, font=("Tahoma", fs(8)), fg="#0a7a2f").pack(
            anchor="w", padx=fs(12)
        )
        tk.Label(
            win, text="ปุ่ม 📋 ดูประวัติ warranty อยู่ใต้ปุ่มบันทึกบนหน้ามือถือ",
            font=("Tahoma", fs(8)), fg="#666", wraplength=fs(420), justify="left",
        ).pack(anchor="w", padx=fs(12), pady=(fs(8), 0))
        tk.Button(win, text="ปิด", font=("Tahoma", fs(10)), command=win.destroy).pack(pady=fs(12))
        win.lift()
        win.focus_force()

    def _open_warranty_form(self, parent, patient_id=None, patient_label="", on_saved=None, existing=None):
        """หน้าเดียวเหมือนมือถือ /warranty."""
        form = tk.Toplevel(parent)
        form.title("เพิ่มประกันอุปกรณ์" + (" (แก้ไข)" if existing else " (หน้าเดียว)"))
        form.transient(parent)

        def wf(n):
            return max(1, round(fs(n) * 1.3))

        form_w, form_h = wf(500), wf(620)
        try:
            sw, sh = form.winfo_screenwidth(), form.winfo_screenheight()
            gap = 58 + 20
            if form_h > sh - gap - 20:
                form_h = max(wf(420), sh - gap - 20)
            x = max(0, (sw - form_w) // 2)
            y = max(0, min((sh - form_h) // 2, sh - form_h - gap))
            form.geometry(f"{form_w}x{form_h}+{x}+{y}")
        except Exception:
            form.geometry(f"{form_w}x{form_h}")
        try:
            form.minsize(wf(440), wf(420))
        except Exception:
            pass

        font_l = ("Tahoma", wf(9))
        font_b = ("Tahoma", wf(10), "bold")
        font_e = ("Tahoma", wf(10))
        label_w = 16

        shell = tk.Frame(form)
        shell.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(shell, orient="vertical")
        canvas = tk.Canvas(shell, highlightthickness=0, yscrollcommand=vsb.set)
        vsb.config(command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _body_cfg(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _canvas_cfg(e):
            canvas.itemconfig(body_id, width=max(1, e.width))

        body.bind("<Configure>", _body_cfg)
        canvas.bind("<Configure>", _canvas_cfg)

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind_w(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _unbind_w(_e=None):
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

        canvas.bind("<Enter>", _bind_w)
        canvas.bind("<Leave>", _unbind_w)
        form.bind("<Destroy>", lambda e: _unbind_w() if e.widget is form else None)

        selected = {"id": int(patient_id) if patient_id else None}

        tk.Label(
            body, text="เพิ่มประกัน — ค้นหาลูกค้า แล้วลงทะเบียนอุปกรณ์ (หน้าเดียว เหมือนมือถือ)",
            font=font_b, fg="#6a4a1a", wraplength=form_w - wf(50), justify="left",
        ).pack(anchor="w", padx=wf(14), pady=(wf(12), wf(6)))

        cust_fr = tk.LabelFrame(body, text=" 1) ลูกค้า (ชื่อ หรือ เบอร์) ", font=font_b, fg="#6a4a1a", padx=wf(8), pady=wf(6))
        cust_fr.pack(fill="x", padx=wf(12), pady=(0, wf(8)))

        cust_search_row = tk.Frame(cust_fr)
        cust_search_row.pack(fill="x", pady=(0, wf(4)))
        v_cust_q = tk.StringVar()
        cust_q_entry = tk.Entry(cust_search_row, textvariable=v_cust_q, font=font_e)
        cust_q_entry.pack(side="left", fill="x", expand=True)
        tk.Button(
            cust_search_row, text="ค้นหา", font=font_l, bg="#6a4a1a", fg="white",
            command=lambda: do_cust_search(),
        ).pack(side="left", padx=(wf(6), 0))

        cust_results = tk.Listbox(cust_fr, font=font_e, height=4, exportselection=False)
        cust_results.pack(fill="x", pady=(0, wf(4)))
        cust_hits = []

        v_sel_label = tk.StringVar(value="(ยังไม่ได้เลือกลูกค้า — ค้นหาหรือกรอกชื่อ/เบอร์ด้านล่าง)")
        tk.Label(cust_fr, textvariable=v_sel_label, font=font_l, fg="#1a7a4a", wraplength=form_w - wf(80), justify="left").pack(anchor="w")

        name_phone_row = tk.Frame(cust_fr)
        name_phone_row.pack(fill="x", pady=(wf(4), 0))
        name_phone_row.columnconfigure(1, weight=1)
        tk.Label(name_phone_row, text="ชื่อลูกค้า", font=font_l, width=10, anchor="w").grid(row=0, column=0, sticky="w")
        v_cust_name = tk.StringVar()
        tk.Entry(name_phone_row, textvariable=v_cust_name, font=font_e).grid(row=0, column=1, sticky="ew", pady=wf(2))
        tk.Label(name_phone_row, text="เบอร์โทร", font=font_l, width=10, anchor="w").grid(row=1, column=0, sticky="w")
        v_cust_phone = tk.StringVar()
        cust_phone_ent = tk.Entry(name_phone_row, textvariable=v_cust_phone, font=font_e)
        cust_phone_ent.grid(row=1, column=1, sticky="ew", pady=wf(2))
        cust_phone_ent.bind("<FocusOut>", lambda e: v_cust_phone.set(storage.format_phone_th(v_cust_phone.get())))

        def set_selected_patient(p):
            selected["id"] = p.get("id")
            v_cust_name.set(p.get("name") or "")
            v_cust_phone.set(storage.format_phone_th(p.get("phone") or "") or (p.get("phone") or ""))
            hn = p.get("hn_code") or ""
            v_sel_label.set(
                f"✓ {p.get('name') or '(ไม่มีชื่อ)'}"
                + (f" · {p.get('phone')}" if p.get("phone") else "")
                + (f" · HN {hn}" if hn else "")
            )

        def do_cust_search():
            term = v_cust_q.get().strip()
            cust_results.delete(0, tk.END)
            cust_hits.clear()
            if not term:
                return
            try:
                hits = storage.search_patients(term, limit=30)
            except Exception as e:
                messagebox.showerror("ค้นหาไม่สำเร็จ", str(e), parent=form)
                return
            if not hits:
                cust_results.insert(tk.END, "(ไม่พบ — กรอกชื่อ/เบอร์ด้านล่าง ระบบจะสร้างใหม่ตอนบันทึก)")
                dig = re.sub(r"\D", "", term)
                if len(dig) >= 9:
                    v_cust_phone.set(storage.format_phone_th(term))
                else:
                    v_cust_name.set(term)
                selected["id"] = None
                v_sel_label.set("(ลูกค้าใหม่ — จะสร้างตอนบันทึก)")
                return
            for p in hits:
                cust_hits.append(p)
                line = p.get("name") or ""
                if p.get("phone"):
                    line += f" · {p.get('phone')}"
                if p.get("hn_code"):
                    line += f" · HN {p.get('hn_code')}"
                cust_results.insert(tk.END, line)

        def on_cust_pick(_e=None):
            sel = cust_results.curselection()
            if not sel or sel[0] >= len(cust_hits):
                return
            set_selected_patient(cust_hits[sel[0]])

        cust_results.bind("<<ListboxSelect>>", on_cust_pick)
        cust_q_entry.bind("<Return>", lambda e: do_cust_search())

        if selected["id"]:
            p0 = storage.get_patient(selected["id"])
            if p0:
                set_selected_patient(p0)
            elif patient_label:
                v_sel_label.set(patient_label)

        equip_fr = tk.LabelFrame(body, text=" 2) อุปกรณ์ / ประกัน ", font=font_b, fg="#6a4a1a", padx=wf(8), pady=wf(6))
        equip_fr.pack(fill="both", expand=True, padx=wf(12), pady=(0, wf(6)))

        grid = tk.Frame(equip_fr)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)

        ex = existing or {}
        vars_map = {}
        widgets = {}

        def add_field(row_i, label, key, default=""):
            tk.Label(grid, text=label, font=font_l, width=label_w, anchor="w").grid(
                row=row_i, column=0, sticky="nw", pady=wf(4), padx=(0, wf(8)),
            )
            var = tk.StringVar(value=default)
            vars_map[key] = var
            ent = tk.Entry(grid, textvariable=var, font=font_e)
            ent.grid(row=row_i, column=1, sticky="ew", pady=wf(4))
            widgets[key] = ent
            return var

        def _num_str(v):
            if v is None or v == "":
                return ""
            try:
                f = float(v)
                return str(int(f)) if f == int(f) else str(f)
            except (TypeError, ValueError):
                return str(v)

        tk.Label(grid, text="ชื่อสินค้า *", font=font_l, width=label_w, anchor="w").grid(
            row=0, column=0, sticky="nw", pady=wf(4), padx=(0, wf(8)),
        )
        product_cell = tk.Frame(grid)
        product_cell.grid(row=0, column=1, sticky="ew", pady=wf(4))
        product_cell.columnconfigure(0, weight=1)
        v_product = tk.StringVar(value=ex.get("product_name") or "")
        vars_map["product"] = v_product
        product_entry = tk.Entry(product_cell, textvariable=v_product, font=font_e)
        product_entry.grid(row=0, column=0, sticky="ew")
        widgets["product"] = product_entry
        product_list = tk.Listbox(
            product_cell, font=font_e, height=6, exportselection=False,
            activestyle="dotbox", relief="solid", bd=1,
        )
        product_list.grid(row=1, column=0, sticky="ew", pady=(wf(2), 0))
        product_list.grid_remove()
        product_hits = []

        v_seller = add_field(1, "ซื้อจาก", "seller", ex.get("seller") or "")
        v_seller_phone = add_field(
            2, "เบอร์ร้าน", "seller_phone",
            storage.format_phone_th(ex.get("seller_phone") or "") if ex.get("seller_phone") else "",
        )
        v_price = add_field(3, "ราคา", "price", _num_str(ex.get("price")))
        v_purchase = add_field(
            4, "วันซื้อ (DD/MM/YY)", "purchase",
            storage._format_date_dmy(ex.get("purchase_date")) or datetime.now().strftime("%d/%m/%y"),
        )
        v_years = add_field(5, "ปีประกัน", "years", _num_str(ex.get("warranty_years")))
        v_expiry = add_field(6, "วันหมดประกัน", "expiry", storage._format_date_dmy(ex.get("expiry_date")))
        v_note = add_field(7, "หมายเหตุ", "note", ex.get("note") or "")

        tk.Label(
            equip_fr,
            text="พิมพ์ชื่อสินค้า → รายการด้านล่าง · เลือกแล้วเติม ซื้อจาก/เบอร์/ราคา/ปีประกัน ตามครั้งล่าสุด\n"
                 "วันหมด = วันซื้อ + ปีประกัน",
            font=("Tahoma", wf(8)), fg="#666", justify="left", wraplength=form_w - wf(60),
        ).pack(anchor="w", pady=(wf(4), 0))

        _applying_product = {"on": False}
        _updating_expiry = {"on": False}

        def hide_product_list():
            product_list.grid_remove()

        def show_product_list(names):
            product_hits.clear()
            product_list.delete(0, tk.END)
            if not names:
                hide_product_list()
                return
            product_hits.extend(names)
            for n in names:
                product_list.insert(tk.END, n)
            product_list.grid()
            product_list.configure(height=min(8, max(3, len(names))))

        def refresh_product_list(*_a):
            if _applying_product["on"]:
                return
            term = v_product.get().strip()
            try:
                names = storage.list_warranty_product_names(term, limit=40)
            except Exception:
                names = []
            show_product_list(names)

        def recompute_expiry(*_a):
            if _updating_expiry["on"]:
                return
            purchase = storage._parse_date_flexible(v_purchase.get())
            ys = v_years.get().strip()
            years = None
            if ys:
                try:
                    years = float(ys)
                except ValueError:
                    years = storage._parse_warranty_years(ys)
            if purchase is None or years is None:
                return
            exp = storage._add_years_to_date(purchase, years)
            if exp is None:
                return
            _updating_expiry["on"] = True
            try:
                v_expiry.set(storage._format_date_dmy(exp))
            finally:
                _updating_expiry["on"] = False

        def apply_product_defaults(name=None):
            name = (name if name is not None else v_product.get()).strip()
            if not name:
                return
            try:
                defaults = storage.get_latest_warranty_defaults(name)
            except Exception:
                defaults = None
            _applying_product["on"] = True
            try:
                v_product.set(name)
                hide_product_list()
                if not defaults:
                    return
                if defaults.get("seller") is not None:
                    v_seller.set(defaults.get("seller") or "")
                sp = defaults.get("seller_phone") or ""
                v_seller_phone.set(storage.format_phone_th(sp) if sp else "")
                pr = defaults.get("price")
                if pr is not None:
                    try:
                        pf = float(pr)
                        v_price.set(str(int(pf)) if pf == int(pf) else str(pf))
                    except (TypeError, ValueError):
                        v_price.set(str(pr))
                wy = defaults.get("warranty_years")
                if wy is not None:
                    try:
                        yf = float(wy)
                        v_years.set(str(int(yf)) if yf == int(yf) else str(yf))
                    except (TypeError, ValueError):
                        v_years.set(str(wy))
                recompute_expiry()
            finally:
                _applying_product["on"] = False

        def on_product_list_click(_e=None):
            sel = product_list.curselection()
            if not sel:
                return
            apply_product_defaults(product_hits[sel[0]])

        def on_product_return(_e=None):
            sel = product_list.curselection()
            if sel:
                apply_product_defaults(product_hits[sel[0]])
                return "break"
            term = v_product.get().strip()
            if term and product_hits:
                if term in product_hits:
                    apply_product_defaults(term)
                else:
                    low = term.casefold()
                    hits = [v for v in product_hits if low in (v or "").casefold()]
                    if len(hits) == 1:
                        apply_product_defaults(hits[0])
                    elif product_hits:
                        apply_product_defaults(product_hits[0])
            return "break"

        def on_product_down(_e=None):
            if not product_hits:
                refresh_product_list()
            if not product_hits:
                return
            if not product_list.winfo_ismapped():
                show_product_list(product_hits)
            product_list.focus_set()
            product_list.selection_clear(0, tk.END)
            product_list.selection_set(0)
            product_list.activate(0)
            return "break"

        def on_purchase_focus_out(_e=None):
            d = storage._parse_date_flexible(v_purchase.get())
            if d:
                v_purchase.set(storage._format_date_dmy(d))
            recompute_expiry()

        def on_expiry_focus_out(_e=None):
            d = storage._parse_date_flexible(v_expiry.get())
            if d:
                v_expiry.set(storage._format_date_dmy(d))

        product_entry.bind("<KeyRelease>", refresh_product_list)
        product_entry.bind("<Return>", on_product_return)
        product_entry.bind("<Down>", on_product_down)
        product_entry.bind("<FocusIn>", lambda e: refresh_product_list())
        product_list.bind("<Double-Button-1>", on_product_list_click)
        product_list.bind("<ButtonRelease-1>", on_product_list_click)
        product_list.bind("<Return>", on_product_return)
        widgets["purchase"].bind("<FocusOut>", on_purchase_focus_out)
        widgets["expiry"].bind("<FocusOut>", on_expiry_focus_out)
        widgets["years"].bind("<KeyRelease>", recompute_expiry)
        widgets["years"].bind("<FocusOut>", recompute_expiry)
        widgets["seller_phone"].bind(
            "<FocusOut>", lambda e: v_seller_phone.set(storage.format_phone_th(v_seller_phone.get()))
        )
        refresh_product_list()
        if not (ex.get("expiry_date")) and v_purchase.get() and v_years.get():
            recompute_expiry()

        def save():
            product = v_product.get().strip()
            if not product:
                messagebox.showwarning("แจ้งเตือน", "ใส่ชื่อสินค้า", parent=form)
                return
            name = v_cust_name.get().strip()
            phone = storage.format_phone_th(v_cust_phone.get())
            v_cust_phone.set(phone)
            pid = selected["id"]
            if pid and name:
                p_sel = storage.get_patient(pid)
                if p_sel and not storage._names_compatible(p_sel.get("name"), name):
                    if storage._normalize_person_name(p_sel.get("name") or "") != storage._normalize_person_name(name):
                        pid = None
            if not pid:
                if not name and not phone:
                    messagebox.showwarning("แจ้งเตือน", "ค้นหาหรือกรอกชื่อ/เบอร์ลูกค้า", parent=form)
                    return
                try:
                    pid, _how = storage.find_or_create_patient_for_warranty(name or phone, phone)
                except Exception as e:
                    messagebox.showerror("สร้างลูกค้าไม่สำเร็จ", str(e), parent=form)
                    return
                if not pid:
                    messagebox.showerror("สร้างลูกค้าไม่สำเร็จ", "ไม่สามารถสร้างแฟ้มลูกค้า", parent=form)
                    return
                selected["id"] = pid
                p = storage.get_patient(pid)
                if p:
                    set_selected_patient(p)
            else:
                if name or phone:
                    try:
                        storage.update_patient_contact(
                            pid, name or (storage.get_patient(pid) or {}).get("name") or "", phone,
                        )
                    except Exception:
                        pass

            price = None
            ps = v_price.get().strip().replace(",", "")
            if ps:
                try:
                    price = float(ps)
                except ValueError:
                    messagebox.showwarning("แจ้งเตือน", "ราคาไม่ถูกต้อง", parent=form)
                    return
            years = None
            ys = v_years.get().strip()
            if ys:
                try:
                    years = float(ys)
                except ValueError:
                    years = storage._parse_warranty_years(ys)
            purchase_date = storage._parse_date_flexible(v_purchase.get())
            if v_purchase.get().strip() and purchase_date is None:
                messagebox.showwarning("แจ้งเตือน", "วันซื้อไม่ถูกต้อง ใช้รูปแบบ DD/MM/YY เช่น 28/07/26", parent=form)
                return
            expiry_date = storage._parse_date_flexible(v_expiry.get())
            if expiry_date is None and purchase_date is not None and years is not None:
                expiry_date = storage._add_years_to_date(purchase_date, years)
            seller_phone = storage.format_phone_th(v_seller_phone.get())
            try:
                if existing and existing.get("id"):
                    storage.update_warranty(
                        existing["id"],
                        product_name=product,
                        seller=v_seller.get().strip() or None,
                        seller_phone=seller_phone or None,
                        price=price,
                        purchase_date=purchase_date,
                        warranty_years=years,
                        expiry_date=expiry_date,
                        note=v_note.get().strip() or None,
                        patient_id=pid,
                    )
                else:
                    storage.add_warranty(
                        patient_id=pid,
                        product_name=product,
                        seller=v_seller.get().strip(),
                        seller_phone=seller_phone,
                        price=price,
                        purchase_date=purchase_date,
                        warranty_years=years,
                        expiry_date=expiry_date,
                        note=v_note.get().strip(),
                        source="manual",
                    )
            except Exception as e:
                messagebox.showerror("บันทึกไม่สำเร็จ", str(e), parent=form)
                return

            p_done = storage.get_patient(pid) or {}
            done_name = p_done.get("name") or name or ""
            done_phone = p_done.get("phone") or phone or ""
            done_hn = p_done.get("hn_code") or ""
            ok_win = tk.Toplevel(form)
            ok_win.title("บันทึกแล้ว")
            ok_win.transient(form)
            ok_win.grab_set()

            def _okf(n):
                return max(1, round(fs(n) * 1.5))

            ow, oh = _okf(340), _okf(220)
            try:
                ox = form.winfo_rootx() + max(0, (form.winfo_width() - ow) // 2)
                oy = form.winfo_rooty() + max(0, (form.winfo_height() - oh) // 2)
                ok_win.geometry(f"{ow}x{oh}+{ox}+{oy}")
            except Exception:
                ok_win.geometry(f"{ow}x{oh}")
            tk.Label(
                ok_win, text="บันทึกประกันเรียบร้อย",
                font=("Tahoma", _okf(14), "bold"), fg="#1a7a4a",
            ).pack(pady=(_okf(14), _okf(6)))
            tk.Label(
                ok_win,
                text=f"{done_name}\n{done_phone}"
                     + (f"\nHN {done_hn}" if done_hn else "")
                     + f"\n{product}",
                font=("Tahoma", _okf(11)), justify="center",
            ).pack(pady=(0, _okf(6)))
            tk.Label(
                ok_win,
                text="รายการใหม่จะอยู่ด้านบนของหน้ารายการ",
                font=("Tahoma", _okf(9)), fg="#555", justify="center",
            ).pack(pady=(0, _okf(10)))

            def _close_ok():
                ok_win.destroy()
                form.destroy()
                if on_saved:
                    on_saved()

            tk.Button(
                ok_win, text="ตกลง", font=("Tahoma", _okf(12), "bold"),
                bg="#1a7a4a", fg="white", width=10, command=_close_ok,
            ).pack(pady=(0, _okf(14)))
            ok_win.protocol("WM_DELETE_WINDOW", _close_ok)
            ok_win.lift()
            ok_win.focus_force()

        btn = tk.Frame(form)
        btn.pack(fill="x", padx=wf(14), pady=(wf(6), wf(20)))
        tk.Button(
            btn, text="💾 บันทึกประกัน", font=font_b, bg="#6a4a1a", fg="white", command=save,
        ).pack(side="right")
        tk.Button(
            btn, text="ยกเลิก", font=("Tahoma", wf(10)), command=form.destroy,
        ).pack(side="right", padx=(0, wf(8)))
        form.lift()
        form.focus_force()
        try:
            if selected["id"]:
                product_entry.focus_set()
            else:
                cust_q_entry.focus_set()
        except Exception:
            pass

    def open_warranty_dialog(self):
        """รายการประกันทั้งหมด + import CSV + ล้างข้อมูลทดสอบ + ใกล้หมดอายุ."""
        win = tk.Toplevel(self.root)
        win.title("🛡 ประกันอุปกรณ์ (หน้าร้าน)")

        def wf(n):
            return max(1, round(fs(n) * 1.2))

        ww, wh = wf(980), wf(620)
        bottom_gap = 48 + 10
        try:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            if wh > sh - bottom_gap:
                wh = max(wf(480), sh - bottom_gap)
            x = max(0, (sw - ww) // 2)
            y = max(0, sh - wh - bottom_gap)
            win.geometry(f"{ww}x{wh}+{x}+{y}")
        except Exception:
            win.geometry(f"{ww}x{wh}")
        try:
            win.minsize(wf(900), wf(480))
        except Exception:
            pass
        win.transient(self.root)

        style = ttk.Style(win)
        try:
            style.configure("Warranty.Treeview", font=("Tahoma", wf(10)), rowheight=max(22, wf(18)))
            style.configure("Warranty.Treeview.Heading", font=("Tahoma", wf(10), "bold"))
        except Exception:
            pass

        filter_var = tk.StringVar(value="all")
        status_var = tk.StringVar(value="")
        rows_cache = []

        top = tk.Frame(win)
        top.pack(fill="x", padx=wf(10), pady=wf(8))
        tk.Label(
            top, text="ประกันอุปกรณ์ — ผูกแฟ้มลูกค้า (patients / HN)",
            font=("Tahoma", wf(11), "bold"),
        ).pack(side="left")
        tk.Label(top, text="  แสดง:", font=("Tahoma", wf(9))).pack(side="left", padx=(wf(12), 0))

        def fmt_day(d):
            return storage._format_date_dmy(d) or "-"

        def days_left(expiry):
            return storage.warranty_days_left(expiry)

        cols = ("hn", "purchase", "customer", "phone", "product", "expiry", "left", "price")
        sortable_cols = {"hn", "purchase", "customer", "product", "expiry", "left"}
        headings = {
            "hn": "HN", "purchase": "วันซื้อ", "customer": "ลูกค้า", "phone": "เบอร์",
            "product": "สินค้า", "expiry": "หมดประกัน", "left": "เหลือ(วัน)", "price": "ราคา",
        }
        widths = {
            "hn": wf(95), "purchase": wf(72), "customer": wf(150), "phone": wf(95),
            "product": wf(200), "expiry": wf(72), "left": wf(80), "price": wf(70),
        }
        anchors = {
            "hn": "center", "purchase": "center", "customer": "w", "phone": "center",
            "product": "w", "expiry": "center", "left": "center", "price": "e",
        }
        sort_state = {"col": None, "reverse": False}

        tree_wrap = tk.Frame(win)
        tree_wrap.pack(fill="both", expand=True, padx=wf(10), pady=(0, wf(4)))
        tree_scroll = tk.Scrollbar(tree_wrap, orient="vertical")
        tree = ttk.Treeview(
            tree_wrap, columns=cols, show="headings", height=16,
            style="Warranty.Treeview", yscrollcommand=tree_scroll.set,
        )
        tree_scroll.config(command=tree.yview)
        tree_scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        for c in cols:
            tree.column(
                c, width=widths[c], minwidth=max(40, widths[c] // 2),
                anchor=anchors[c], stretch=(c == "product"),
            )
            tree.heading(c, anchor=anchors[c])

        btns = tk.Frame(win)
        btns.pack(fill="x", padx=wf(10), pady=(wf(4), wf(6)))
        status_lab = tk.Label(win, textvariable=status_var, font=("Tahoma", wf(9)), fg="#555")
        status_lab.pack(anchor="w", padx=wf(10), pady=(0, wf(14)))

        def refill_tree():
            tree.delete(*tree.get_children())
            for w in rows_cache:
                expiry_val = w.get("expiry_date")
                n = days_left(expiry_val)
                left_s = "—" if n is None else str(n)
                price = w.get("price")
                price_s = "" if price is None else f"{float(price):.0f}"
                tree.insert("", tk.END, iid=str(w["id"]), values=(
                    w.get("hn_code") or "",
                    fmt_day(w.get("purchase_date")),
                    w.get("patient_name") or "",
                    w.get("patient_phone") or "",
                    w.get("product_name") or "",
                    fmt_day(expiry_val),
                    left_s,
                    price_s,
                ))

        def update_heading_labels():
            for c in cols:
                base = headings[c]
                anc = anchors[c]
                if c not in sortable_cols:
                    tree.heading(c, text=base, anchor=anc, command=lambda: None)
                    continue
                arrow = ""
                if sort_state["col"] == c:
                    arrow = " ▼" if sort_state["reverse"] else " ▲"
                tree.heading(c, text=base + arrow, anchor=anc, command=lambda col=c: sort_by(col))

        def _sort_key(col, w):
            if col == "hn":
                return (w.get("hn_code") or "").casefold()
            if col == "customer":
                return (w.get("patient_name") or "").casefold()
            if col == "product":
                return (w.get("product_name") or "").casefold()
            if col == "purchase":
                d = w.get("purchase_date")
                return (d is None or d == "", d or "")
            if col == "expiry":
                d = w.get("expiry_date")
                return (d is None or d == "", d or "")
            if col == "left":
                n = days_left(w.get("expiry_date"))
                return (n is None, n if n is not None else 0)
            return ""

        def sort_by(col):
            if col not in sortable_cols:
                return
            if sort_state["col"] == col:
                sort_state["reverse"] = not sort_state["reverse"]
            else:
                sort_state["col"] = col
                sort_state["reverse"] = False
            rows_cache.sort(key=lambda w: _sort_key(col, w), reverse=sort_state["reverse"])
            refill_tree()
            update_heading_labels()

        def apply_current_sort():
            col = sort_state["col"]
            if col and col in sortable_cols and rows_cache:
                rows_cache.sort(key=lambda w: _sort_key(col, w), reverse=sort_state["reverse"])

        update_heading_labels()

        def refresh():
            rows_cache.clear()
            mode = filter_var.get()
            try:
                if mode == "30":
                    data = storage.list_warranties(expiring_within_days=30)
                elif mode == "90":
                    data = storage.list_warranties(expiring_within_days=90)
                else:
                    data = storage.list_warranties()
            except Exception as e:
                status_var.set(f"โหลดไม่ได้: {e}")
                return
            rows_cache.extend(data)
            apply_current_sort()
            refill_tree()
            update_heading_labels()
            status_var.set(f"ทั้งหมด {len(data)} รายการ")

        def do_import():
            guess = os.path.join(os.path.dirname(SCRIPT_DIR), "Warranty_tracker")
            if not os.path.isdir(guess):
                guess = SCRIPT_DIR
            path = filedialog.askopenfilename(
                title="เลือกไฟล์ export warranty CSV",
                parent=win,
                initialdir=guess if os.path.isdir(guess) else SCRIPT_DIR,
                filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            )
            if not path:
                return
            if not messagebox.askyesno(
                "ยืนยัน import",
                f"นำเข้าจาก:\n{path}\n\n"
                "จะสร้างแฟ้มลูกค้า (+HN) ถ้ายังไม่มี แล้วเพิ่มรายการประกัน\n"
                "(ข้ามรายการที่ import ซ้ำแล้ว)\n\n"
                "⚠️ ถ้าทดสอบเสร็จก่อนส่งให้ลูกค้า ใช้ปุ่ม «ล้าง import/ทดสอบ» ได้",
                parent=win,
            ):
                return
            try:
                report = storage.import_warranties_from_csv(path)
            except Exception as e:
                messagebox.showerror("Import ไม่สำเร็จ", str(e), parent=win)
                return
            msg = (
                f"แถวในไฟล์: {report['rows']}\n"
                f"เพิ่มประกัน: {report['inserted']}\n"
                f"ข้ามซ้ำ: {report['skipped_dup']}\n"
                f"แถวไม่ครบ: {report['skipped_bad']}\n"
                f"สร้างแฟ้มลูกค้าใหม่: {report['patients_created']}\n"
                f"จับคู่แฟ้มเดิม: {report['patients_matched']}\n"
            )
            if report["errors"]:
                msg += "\nข้อผิดพลาด:\n" + "\n".join(report["errors"][:8])
            messagebox.showinfo("ผล import", msg, parent=win)
            refresh()

        def do_clear_import_test():
            n = storage.count_warranties()
            if not messagebox.askyesno(
                "ล้าง import / ทดสอบ",
                "ลบรายการประกันที่มาจาก Import CSV และ source=test เท่านั้น\n"
                "(รายการที่พนักงานบันทึก manual/mobile จริงจะยังอยู่)\n"
                "และลบแฟ้มลูกค้าที่ไม่มีประวัติพิมพ์ฉลากค้างอยู่\n\n"
                f"ประกันทั้งหมดในระบบตอนนี้: {n} รายการ\n\nยืนยันลบ import/test?",
                parent=win,
            ):
                return
            try:
                r = storage.clear_imported_and_test_warranties(also_orphan_patients=True)
            except Exception as e:
                messagebox.showerror("ล้างไม่สำเร็จ", str(e), parent=win)
                return
            messagebox.showinfo(
                "ล้างแล้ว",
                f"ลบประกัน: {r['warranties_deleted']}\nลบแฟ้มลูกค้าที่ไม่มีประวัติ: {r['patients_deleted']}",
                parent=win,
            )
            refresh()

        def do_clear_all():
            n = storage.count_warranties()
            if not messagebox.askyesno(
                "⚠️ ล้างประกันทั้งหมด",
                f"จะลบประกันทุกแถว ({n} รายการ) — ทั้ง import / มือถือ / desktop\n"
                "และลบแฟ้มลูกค้าที่ไม่มีประวัติพิมพ์ฉลาก\n\n"
                "ใช้ก่อนส่งเครื่องให้ลูกค้าถ้าเคยโหลดข้อมูลทดสอบ\n\nยืนยันลบทั้งหมด?",
                parent=win,
            ):
                return
            if not messagebox.askyesno(
                "ยืนยันอีกครั้ง",
                "กด ใช่ = ลบประกันทั้งหมดจริง\nกู้คืนไม่ได้",
                parent=win,
            ):
                return
            try:
                r = storage.clear_all_warranties(also_orphan_patients=True)
            except Exception as e:
                messagebox.showerror("ล้างไม่สำเร็จ", str(e), parent=win)
                return
            messagebox.showinfo(
                "ล้างแล้ว",
                f"ลบประกัน: {r['warranties_deleted']}\nลบแฟ้มลูกค้า: {r['patients_deleted']}",
                parent=win,
            )
            refresh()

        def open_patient_of_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("แจ้งเตือน", "เลือกรายการประกันก่อน แล้วค่อยเปิดแฟ้มลูกค้า", parent=win)
                return
            wid = int(sel[0])
            w = next((x for x in rows_cache if x["id"] == wid), None)
            if not w or not w.get("patient_id"):
                return
            self.open_patient_profile_dialog(preload_patient_id=w["patient_id"])

        def add_warranty_via_patient():
            self._open_warranty_form(parent=win, patient_id=None, patient_label="", on_saved=refresh)

        def delete_selected():
            sel = tree.selection()
            if not sel:
                return
            wid = int(sel[0])
            w = next((x for x in rows_cache if x["id"] == wid), None)
            label = (w or {}).get("product_name") or wid
            if not messagebox.askyesno("ยืนยัน", f"ลบรายการประกัน:\n{label} ?", parent=win):
                return
            storage.delete_warranty(wid)
            refresh()

        for val, lab in (("all", "ทั้งหมด"), ("30", "หมดใน 30 วัน"), ("90", "หมดใน 90 วัน")):
            tk.Radiobutton(
                top, text=lab, variable=filter_var, value=val, font=("Tahoma", wf(9)),
                command=refresh,
            ).pack(side="left", padx=wf(4))

        tk.Button(
            btns, text="＋ เพิ่มประกัน", font=("Tahoma", wf(9), "bold"),
            bg="#6a4a1a", fg="white", command=add_warranty_via_patient,
        ).pack(side="left")
        tk.Button(
            btns, text="📱 มือถือ", font=("Tahoma", wf(9), "bold"),
            bg="#5a5a9a", fg="white", command=self.open_warranty_mobile_info_dialog,
        ).pack(side="left", padx=(wf(6), 0))
        tk.Button(
            btns, text="📥 Import CSV", font=("Tahoma", wf(9), "bold"),
            bg="#1a5a9a", fg="white", command=do_import,
        ).pack(side="left", padx=(wf(6), 0))
        tk.Button(
            btns, text="🧹 ล้าง import/ทดสอบ", font=("Tahoma", wf(9)),
            fg="#8a4a00", command=do_clear_import_test,
        ).pack(side="left", padx=(wf(6), 0))
        tk.Button(
            btns, text="🗑 ล้างทั้งหมด", font=("Tahoma", wf(9)),
            fg="#a00", command=do_clear_all,
        ).pack(side="left", padx=(wf(6), 0))
        tk.Button(btns, text="🔄 รีเฟรช", font=("Tahoma", wf(9)), command=refresh).pack(
            side="left", padx=wf(6)
        )
        tk.Button(
            btns, text="🗂 เปิดแฟ้มลูกค้า", font=("Tahoma", wf(9)),
            command=open_patient_of_selected,
        ).pack(side="left", padx=wf(6))
        tk.Button(btns, text="ลบที่เลือก", font=("Tahoma", wf(9)), command=delete_selected).pack(
            side="left", padx=wf(6)
        )
        tk.Button(btns, text="ปิด", font=("Tahoma", wf(9)), command=win.destroy).pack(side="right")

        refresh()
        win.lift()
        win.focus_force()
