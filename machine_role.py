"""Per-machine role for LabelPrinterStandalone_MobileQueue (like PharmacyPOS / HOPE).

Stored under %LOCALAPPDATA%\\LabelPrinterStandalone_MobileQueue\\ next to data.db.

  label_app  — open full desktop GUI (queue, edit drugs, warranty, etc.)
  print_host — accept LAN print jobs (print_host_server)

Both can be true on the same PC (typical single-station shop).
SQLite stays local on this PC only — multi-station shared DB is not the model here.
"""
import json
import os

try:
    from storage import APP_DATA_DIR as LOCAL_APP_DATA_DIR
except Exception:
    LOCAL_APP_DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "LabelPrinterStandalone_MobileQueue",
    )

ROLE_PATH = os.path.join(LOCAL_APP_DATA_DIR, "role.json")

ROLE_DEFAULTS = {
    "label_app": True,
    "print_host": True,
    "print_host_port": 8970,
    "station_id": "",
    "station_name": "",
}


def load_role():
    if not os.path.isfile(ROLE_PATH):
        return None
    try:
        with open(ROLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(ROLE_DEFAULTS)
        merged.update(data or {})
        return merged
    except Exception:
        return None


def save_role(role):
    os.makedirs(LOCAL_APP_DATA_DIR, exist_ok=True)
    merged = dict(ROLE_DEFAULTS)
    merged.update(role or {})
    with open(ROLE_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def ask_role_gui():
    """First-run dialog: which roles this PC performs."""
    import tkinter as tk

    result = dict(ROLE_DEFAULTS)

    root = tk.Tk()
    root.title("พิมพ์ฉลากยา - ตั้งค่าเครื่องนี้ครั้งแรก")
    root.resizable(False, False)

    tk.Label(
        root, text="เครื่องนี้ทำหน้าที่อะไรบ้าง? (ติ๊กได้มากกว่า 1 ข้อ)",
        font=("Tahoma", 12, "bold"), pady=14, padx=24,
    ).pack()

    label_var = tk.BooleanVar(value=True)
    print_var = tk.BooleanVar(value=True)

    tk.Checkbutton(
        root,
        text=(
            "โปรแกรมฉลากยา (Label App)\n"
            "เปิดหน้าต่างเต็ม: แก้ยา, คิวมือถือ, ประวัติ, ประกัน ฯลฯ"
        ),
        variable=label_var, font=("Tahoma", 10), anchor="w", justify="left", padx=24,
    ).pack(fill="x")
    tk.Checkbutton(
        root,
        text=(
            "Print host — รับงานพิมพ์จากมือถือ/เครื่องอื่นบน WiFi ร้าน\n"
            "ติ๊กถ้าเครื่องนี้ต่อเครื่องพิมพ์ฉลากอยู่ (พิมพ์เลยโดยไม่รอคิว)"
        ),
        variable=print_var, font=("Tahoma", 10), anchor="w", justify="left", padx=24,
    ).pack(fill="x", pady=(4, 0))

    tk.Label(
        root,
        text=(
            "ร้านเครื่องเดียว: ติ๊กทั้งสองข้อ\n"
            "เครื่องเพิ่มที่มีแค่เครื่องพิมพ์: ติ๊กแค่ Print host\n"
            "ข้อมูล SQLite อยู่เครื่องนี้เท่านั้น (ไม่แชร์ข้าม PC)\n"
            "แก้ทีหลังได้จาก ⚙️ ตั้งค่า → บทบาทเครื่อง"
        ),
        font=("Tahoma", 9), fg="#666666", justify="left", padx=24, pady=14,
    ).pack()

    def on_ok():
        result["label_app"] = bool(label_var.get())
        result["print_host"] = bool(print_var.get())
        root.destroy()

    tk.Button(root, text="ตกลง", command=on_ok, font=("Tahoma", 11), width=14, pady=6).pack(pady=(0, 16))
    root.protocol("WM_DELETE_WINDOW", on_ok)
    root.update_idletasks()
    w, h = max(root.winfo_width(), 440), max(root.winfo_height(), 300)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
    root.mainloop()
    return result


def run_print_only_window(ip, port, station_name="", printer_name=""):
    """Keep process alive when only print_host is enabled (no full Label App)."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("Label Printer — Print host")
    root.geometry("440x260")
    root.resizable(False, False)

    url = f"http://{ip}:{port}" if ip and port else "(เปิดไม่สำเร็จ)"
    tk.Label(root, text="🖨 Print host กำลังรอรับงานพิมพ์", font=("Tahoma", 13, "bold"), fg="#1a7a4a").pack(
        pady=(18, 8)
    )
    tk.Label(root, text=f"Station: {station_name or '(ไม่ตั้งชื่อ)'}", font=("Tahoma", 10)).pack()
    tk.Label(root, text=f"Printer: {printer_name or '(ยังไม่เลือก)'}", font=("Tahoma", 10)).pack()
    tk.Label(root, text=f"URL: {url}", font=("Tahoma", 11, "bold"), fg="#1a5a9a").pack(pady=8)
    tk.Label(
        root, text="เปิดหน้าต่างนี้ค้างไว้ระหว่างใช้งาน\nมือถือต้องอยู่ WiFi ร้านเดียวกับเครื่องนี้",
        font=("Tahoma", 9), fg="#555", justify="center",
    ).pack(pady=6)

    def copy_url():
        if not ip or not port:
            return
        root.clipboard_clear()
        root.clipboard_append(url)
        messagebox.showinfo("คัดลอกแล้ว", url, parent=root)

    tk.Button(root, text="📋 คัดลอก URL", font=("Tahoma", 10), command=copy_url).pack(pady=4)
    tk.Button(root, text="ปิด (หยุดรับพิมพ์)", font=("Tahoma", 10), command=root.destroy).pack(pady=(4, 16))
    root.mainloop()
