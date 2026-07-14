"""Per-installation settings: printer, label size, company/pharmacist info.
No hardcoded printer name or company details - every shop configures its own
via the settings dialog on first run."""
import json
import os

from storage import APP_DATA_DIR

SETTINGS_PATH = os.path.join(APP_DATA_DIR, "settings.json")

DEFAULTS = {
    "printer_name": "",
    "label_w_mm": 80,
    "label_h_mm": 50,
    "paper_mode": "thermal",  # "thermal" (roll printer, 1 label = 1 page) or "a4" (tiled grid on A4)
    "company_name": "",
    "address_line1": "",
    "address_line2": "",
    "phone": "",
    "pharmacist_names": "",
}


def settings_exist():
    return os.path.isfile(SETTINGS_PATH)


def load_settings():
    if not settings_exist():
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULTS)


def save_settings(settings):
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def list_printers():
    import win32print
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    try:
        printers = win32print.EnumPrinters(flags)
        return sorted(p[2] for p in printers)
    except Exception:
        return []
