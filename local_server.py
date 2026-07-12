"""Local-LAN HTTP server so staff phones (same WiFi as this PC) can submit
drugs into the print queue - no cloud/internet dependency, matches the "one
PC, one printer" assumption of this standalone build. Stdlib only
(http.server) so no extra dependency needs to ride along in the PyInstaller
build.

Trust model: this server is only meant to be reachable on the shop's own
WiFi, so there is deliberately no login/auth - anyone on the LAN can submit
a job or manage the staff-name list. If that's ever not an acceptable
tradeoff for a given shop, this is the place to add a shared PIN check.
"""
import json
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import storage
import app_settings

PORT_RANGE = range(8869, 8879)

# Favorites live in favorites.json next to the SQLite DB (same file
# label_gui.py's load_favorites()/save_favorites() use) - read directly here
# rather than importing label_gui, which would create a circular import
# (label_gui imports this module to start the server). Since this is a
# single-PC app, one file being read here and written there is not a race
# condition worth worrying about the way it was for the multi-station shop
# version (see project-label-printer-hybrid memory) - there's no BeeStation-
# style background sync in the picture at all.
FAVORITES_PATH = os.path.join(storage.APP_DATA_DIR, "favorites.json")


def read_favorites():
    if not os.path.isfile(FAVORITES_PATH):
        return {}
    try:
        with open(FAVORITES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_lan_ip():
    """Best-effort LAN IP for this PC. The UDP "connect" here never actually
    sends a packet (UDP is connectionless) - it just asks the OS which local
    interface/IP it would use to reach that address, which is exactly the
    LAN-facing IP phones on the same WiFi need to type in."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def build_search_results(term):
    hits = storage.search_templates(term)
    out = []
    for h in hits:
        info = storage.get_template(h["idproduct"]) or {}
        # bool(info) alone isn't enough - a row can exist with only drug1
        # filled in (e.g. from a bulk Excel import of names), which isn't
        # real dosing info. Same check the desktop app uses (has_dosing_data).
        has_info = storage.has_dosing_data(info)
        out.append({
            "idproduct": h["idproduct"],
            "name": h["name"],
            "hasInfo": has_info,
            "drug1": info.get("drug1") or h["name"],
            "drug2": info.get("drug2", ""),
            "note": info.get("note", ""),
            "qty": info.get("qty", ""),
            "unit": info.get("unit") or "เม็ด",
            "per_day": info.get("per_day", ""),
            "every_hr": info.get("every_hr", ""),
            "meal": info.get("meal") or "หลังอาหาร",
            "times": info.get("times") or [],
            "extra_labels": info.get("extra_labels") or [],
            "usage_mode": info.get("usage_mode") or "oral",
        })
    return out


def parse_multipart(content_type, body):
    """Minimal multipart/form-data parser - Python's stdlib `cgi` module
    (which used to handle this) was removed in 3.13. We fully control both
    ends (the mobile page's own upload form), so this only needs to handle
    the shapes a normal browser FormData upload actually produces, not every
    edge case in RFC 2388. Returns (fields: {name: str}, files: {name: bytes}).
    """
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return {}, {}
    boundary = m.group(1).strip('"').encode()
    fields, files = {}, {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="replace")
        content = part[header_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        name_m = re.search(r'name="([^"]*)"', headers_raw)
        if not name_m:
            continue
        field_name = name_m.group(1)
        filename_m = re.search(r'filename="([^"]*)"', headers_raw)
        if filename_m:
            if filename_m.group(1):  # empty filename = no file actually chosen
                files[field_name] = content
        else:
            fields[field_name] = content.decode("utf-8", errors="replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep this quiet - fires on every phone request

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type="image/jpeg"):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/":
            self._send_html(QUEUE_PAGE_HTML)

        elif parsed.path == "/api/search":
            q = qs.get("q", [""])[0]
            if len(q.strip()) < 1:
                self._send_json([])
                return
            self._send_json(build_search_results(q))

        elif parsed.path == "/api/favorites":
            favorites = read_favorites()
            name = qs.get("name", [""])[0]
            if name:
                if name not in favorites:
                    self._send_json({"ok": False, "message": "ไม่พบ Favorite นี้"}, 404)
                    return
                self._send_json({"ok": True, "name": name, "drugs": favorites[name]})
            else:
                out = [{"name": n, "count": len(d)} for n, d in favorites.items()]
                out.sort(key=lambda r: r["name"])
                self._send_json(out)

        elif parsed.path == "/api/settings":
            settings = app_settings.load_settings()
            self._send_json({
                "company_name": settings.get("company_name") or "",
                "phone": settings.get("phone") or "",
                "address_line1": settings.get("address_line1") or "",
                "address_line2": settings.get("address_line2") or "",
                "pharmacist_names": settings.get("pharmacist_names") or "",
            })

        elif parsed.path == "/api/staff":
            self._send_json(storage.list_staff_names())

        elif parsed.path == "/api/patients":
            q = qs.get("q", [""])[0].strip()
            self._send_json(storage.search_patients(q) if q else [])

        elif parsed.path == "/api/patient":
            try:
                patient_id = int(qs.get("id", [""])[0])
            except (ValueError, IndexError):
                self._send_json({"ok": False, "message": "รหัสผู้ป่วยไม่ถูกต้อง"}, 400)
                return
            patient = storage.get_patient(patient_id)
            if not patient:
                self._send_json({"ok": False, "message": "ไม่พบผู้ป่วย"}, 404)
                return
            jobs = storage.list_print_jobs_for_patient(patient["name"], patient["phone"])
            docs = storage.list_patient_documents(patient_id)
            self._send_json({"ok": True, "patient": patient, "jobs": jobs, "documents": docs})

        elif parsed.path.startswith("/patient_docs/"):
            # /patient_docs/<patient_id>/<filename> - patient_id forced to int
            # and filename reduced to its basename so this can never escape
            # PATIENT_DOCS_DIR (no path traversal via ../../ etc.)
            parts = parsed.path.split("/")
            if len(parts) != 4:
                self.send_response(404)
                self.end_headers()
                return
            try:
                patient_id = int(parts[2])
            except ValueError:
                self.send_response(404)
                self.end_headers()
                return
            filename = os.path.basename(parts[3])
            full_path = os.path.join(storage.PATIENT_DOCS_DIR, str(patient_id), filename)
            self._send_file(full_path)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/submit":
            body = self._read_json_body()
            if body is None:
                self._send_json({"ok": False, "message": "รูปแบบข้อมูลไม่ถูกต้อง"}, 400)
                return
            drugs = body.get("drugs") or []
            if not drugs:
                self._send_json({"ok": False, "message": "กรุณาเลือกยาอย่างน้อย 1 รายการ"}, 400)
                return
            job_id = storage.add_queue_job(
                body.get("patient_name", ""), body.get("customer_phone", ""), drugs,
                has_allergy=bool(body.get("has_allergy")),
            )
            self._send_json({"ok": True, "id": job_id})

        elif self.path == "/api/staff_add":
            body = self._read_json_body()
            name = (body or {}).get("name", "")
            staff_id = storage.add_staff_name(name)
            if staff_id is None:
                self._send_json({"ok": False, "message": "กรุณาใส่ชื่อพนักงาน"}, 400)
                return
            self._send_json({"ok": True, "id": staff_id})

        elif self.path == "/api/staff_delete":
            body = self._read_json_body()
            staff_id = (body or {}).get("id")
            if not staff_id:
                self._send_json({"ok": False, "message": "ไม่พบรหัสพนักงาน"}, 400)
                return
            storage.delete_staff_name(staff_id)
            self._send_json({"ok": True})

        elif self.path == "/api/patient_create":
            body = self._read_json_body() or {}
            patient_id = storage.find_or_create_patient(body.get("name", ""), body.get("phone", ""))
            if patient_id is None:
                self._send_json({"ok": False, "message": "กรุณาใส่ชื่อหรือเบอร์โทรอย่างน้อยหนึ่งอย่าง"}, 400)
                return
            self._send_json({"ok": True, "id": patient_id})

        elif self.path == "/api/patient_allergy":
            body = self._read_json_body() or {}
            patient_id = body.get("id")
            if not patient_id:
                self._send_json({"ok": False, "message": "ไม่พบรหัสผู้ป่วย"}, 400)
                return
            storage.update_patient_allergy(patient_id, body.get("allergy_note", ""))
            self._send_json({"ok": True})

        elif self.path == "/api/patient_upload":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            fields, files = parse_multipart(self.headers.get("Content-Type", ""), raw)
            try:
                patient_id = int(fields.get("patient_id", ""))
            except ValueError:
                self._send_json({"ok": False, "message": "ไม่พบรหัสผู้ป่วย"}, 400)
                return
            image_bytes = files.get("image")
            if not image_bytes:
                self._send_json({"ok": False, "message": "กรุณาเลือกรูปภาพ"}, 400)
                return
            try:
                storage.add_patient_document(patient_id, image_bytes, fields.get("note", ""))
            except Exception as e:
                self._send_json({"ok": False, "message": f"อัปโหลดไม่สำเร็จ: {e}"}, 400)
                return
            self._send_json({"ok": True})

        elif self.path == "/api/patient_doc_delete":
            body = self._read_json_body() or {}
            doc_id = body.get("id")
            if not doc_id:
                self._send_json({"ok": False, "message": "ไม่พบรหัสเอกสาร"}, 400)
                return
            storage.delete_patient_document(doc_id)
            self._send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()


_server = None


def start_server():
    """Starts the local HTTP server on a background daemon thread (first
    free port in PORT_RANGE). Returns (ip, port) for display in the app, or
    (None, None) if every port in range was already taken."""
    global _server
    for port in PORT_RANGE:
        try:
            _server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            break
        except OSError:
            continue
    else:
        return None, None
    thread = threading.Thread(target=_server.serve_forever, daemon=True)
    thread.start()
    return get_lan_ip(), _server.server_address[1]


QUEUE_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>คิวพิมพ์ฉลากยา</title>
<style>
  :root { --primary:#1a7a4a; --text:#1a1a2e; --muted:#6b7280; --border:#e5e7eb; --bg:#f8faf9; --danger:#dc2626; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--text); -webkit-tap-highlight-color:transparent; }
  .header { background:var(--primary); color:#fff; position:sticky; top:0; z-index:20; }
  .header-top { padding:14px 16px; display:flex; align-items:center; justify-content:space-between; }
  .header h1 { font-size:18px; margin:0; font-weight:700; }
  .header button { background:rgba(255,255,255,0.15); border:none; color:#fff; padding:8px 14px; border-radius:8px; font-size:13px; font-weight:600; cursor:pointer; }
  .staff-bar { background:#1d4ed8; color:#fff; font-size:20px; font-weight:800; text-align:center; padding:8px 16px; }

  /* Staff picker screen */
  #staff-screen { display:none; min-height:100vh; flex-direction:column; align-items:center; justify-content:center; padding:24px; background:var(--primary); }
  #staff-screen.show { display:flex; }
  .staff-box { background:#fff; border-radius:16px; padding:24px; width:100%; max-width:400px; box-shadow:0 10px 30px rgba(0,0,0,0.2); }
  .staff-box h2 { margin:0 0 4px; font-size:20px; color:var(--primary); }
  .staff-box p { margin:0 0 16px; font-size:13px; color:var(--muted); }
  .staff-grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:10px; margin-bottom:14px; }
  .staff-btn { padding:16px 6px; font-size:15px; font-weight:700; color:var(--primary); background:var(--bg); border:1.5px solid var(--border); border-radius:12px; cursor:pointer; position:relative; }
  .staff-btn:active { background:var(--primary); color:#fff; border-color:var(--primary); }
  .staff-btn .staff-del { position:absolute; top:-10px; right:-10px; width:30px; height:30px; border-radius:50%; background:var(--danger); color:#fff; font-size:15px; line-height:30px; text-align:center; display:none; box-shadow:0 1px 4px rgba(0,0,0,0.3); }
  .staff-edit .staff-del { display:block; }
  .staff-add-row { display:flex; gap:8px; margin-bottom:10px; }
  .staff-add-row input { flex:1; padding:10px; font-size:14px; border:1.5px solid var(--border); border-radius:8px; }
  .staff-add-row button { padding:10px 14px; font-size:13px; font-weight:700; color:#fff; background:var(--primary); border:none; border-radius:8px; cursor:pointer; }
  .staff-edit-toggle { width:100%; padding:10px; font-size:13px; font-weight:700; color:var(--primary); background:var(--bg); border:1.5px solid var(--primary); border-radius:8px; cursor:pointer; }
  .staff-edit-toggle.active { color:#fff; background:var(--danger); border-color:var(--danger); }
  .confirm-msg { font-size:15px; margin-bottom:16px; }
  .confirm-btn-row { display:flex; gap:8px; }
  .confirm-btn-row button { flex:1; padding:12px; font-size:15px; font-weight:700; border:none; border-radius:8px; cursor:pointer; }
  .confirm-yes { background:var(--danger); color:#fff; }
  .confirm-no { background:#e5e7eb; color:var(--text); }

  /* App screen */
  #app-screen { display:none; padding-bottom:100px; }
  #app-screen.show { display:block; }

  .section { padding:14px 16px; }
  .section-title { font-size:14px; font-weight:700; color:var(--muted); margin-bottom:8px; }
  .selected-header { display:flex; align-items:center; justify-content:space-between; }
  .selected-header .section-title { margin-bottom:8px; }
  #clear-all-btn { background:none; border:none; color:var(--danger); font-size:17px; font-weight:700; cursor:pointer; padding:4px 0 8px; }
  #clear-all-btn:active { opacity:.6; }
  #allergy-toggle { display:flex; align-items:center; gap:4px; font-size:17px; font-weight:700; color:var(--danger); cursor:pointer; padding:4px 8px 8px 0; }
  #allergy-toggle input { width:16px; height:16px; }

  #fav-open-btn, #patient-open-btn { width:100%; padding:12px; font-size:15px; font-weight:700; color:var(--primary); background:#fff; border:1.5px solid var(--primary); border-radius:10px; cursor:pointer; }
  #fav-open-btn:active, #patient-open-btn:active { background:var(--bg); }

  .patient-detail { display:none; margin-top:14px; border-top:1px solid var(--border); padding-top:12px; }
  .patient-detail-name { font-size:17px; font-weight:700; color:var(--primary); margin-bottom:8px; }
  .patient-section-title { font-weight:700; font-size:13px; margin-bottom:4px; }
  #patient-allergy-input { width:100%; padding:8px; border:1.5px solid var(--border); border-radius:8px; font-family:inherit; font-size:14px; resize:vertical; }
  .patient-save-btn { margin:6px 0 14px; padding:9px 14px; background:var(--primary); color:#fff; border:none; border-radius:8px; font-weight:700; cursor:pointer; }
  .purchase-item { padding:8px 0; border-bottom:1px solid var(--border); font-size:13px; }
  .doc-header-row { display:flex; justify-content:space-between; align-items:center; }
  .doc-upload-btn { padding:7px 12px; background:#1a5a9a; color:#fff; border:none; border-radius:8px; font-size:12px; font-weight:700; cursor:pointer; }
  .doc-item { display:flex; gap:10px; align-items:center; padding:8px 0; border-bottom:1px solid var(--border); }
  .doc-thumb { width:42px; height:42px; object-fit:cover; border-radius:6px; border:1px solid var(--border); flex-shrink:0; }
  .doc-note { flex:1; font-size:10px; min-width:0; overflow-wrap:break-word; word-break:break-word; }
  .upload-preview-box { border:1.5px dashed var(--border); border-radius:10px; padding:10px; margin-top:8px; }
  .upload-preview-box img { max-width:100%; max-height:160px; display:block; margin:0 auto 8px; border-radius:6px; }
  #patient-upload-note { width:100%; padding:8px; border:1.5px solid var(--border); border-radius:8px; font-family:inherit; font-size:13px; resize:vertical; margin-bottom:8px; box-sizing:border-box; }
  .upload-btn-row { display:flex; gap:8px; }
  .upload-save-btn { flex:1; padding:9px; background:var(--primary); color:#fff; border:none; border-radius:8px; font-weight:700; cursor:pointer; }
  .upload-cancel-btn { flex:1; padding:9px; background:#e5e7eb; color:var(--text); border:none; border-radius:8px; font-weight:700; cursor:pointer; }
  .doc-del-btn { background:none; border:none; color:var(--danger); font-size:20px; cursor:pointer; padding:4px; }

  #search-input { width:100%; padding:14px; font-size:17px; border:1.5px solid var(--border); border-radius:10px; }
  #search-input:focus { outline:none; border-color:var(--primary); }
  #search-results { margin-top:8px; }
  .result-item { display:flex; align-items:center; gap:10px; padding:14px 12px; background:#fff; border:1px solid var(--border); border-radius:10px; margin-bottom:6px; cursor:pointer; }
  .result-item:active { background:var(--bg); }
  .dot { width:11px; height:11px; border-radius:50%; flex-shrink:0; }
  .dot.green { background:#16a34a; }
  .dot.red { background:var(--danger); }
  .result-name { flex:1; font-size:15px; line-height:1.4; }
  .result-hint { font-size:11px; color:var(--muted); }
  #selected-list { margin-top:4px; }
  .selected-item { background:#fff; border:2px solid var(--primary); border-radius:10px; padding:12px 14px; margin-bottom:8px; position:relative; }
  .selected-item.has-info { border-color:#15803d; }
  .selected-item.no-info { border-color:#b91c1c; }
  .selected-item .drug-name { font-size:16px; font-weight:700; padding-right:64px; }
  .selected-item .preview-btn { position:absolute; top:8px; right:42px; background:none; border:none; color:var(--primary); font-size:19px; cursor:pointer; line-height:1; padding:4px; }
  .selected-item .remove-btn { position:absolute; top:10px; right:10px; background:none; border:none; color:var(--danger); font-size:20px; cursor:pointer; line-height:1; padding:4px; }
  .selected-item .dose-preview { font-size:13px; color:var(--muted); margin-top:4px; }
  .empty-hint { color:var(--muted); font-size:13px; padding:10px 4px; }

  /* Modals (favorites picker + label preview) share this overlay pattern */
  .overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:200; align-items:center; justify-content:center; padding:16px; }
  .overlay.show { display:flex; }
  .modal-card { background:#fff; border-radius:12px; padding:16px; max-width:440px; width:100%; max-height:85vh; overflow-y:auto; }
  .modal-card h3 { margin:0 0 12px; font-size:17px; }
  .fav-item { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:14px 12px; background:var(--bg); border:1px solid var(--border); border-radius:10px; margin-bottom:8px; cursor:pointer; }
  .fav-item:active { background:#eef2f1; }
  .fav-item .fav-name { font-size:15px; font-weight:600; }
  .fav-item .fav-count { font-size:12px; color:var(--muted); }
  .modal-close-btn { width:100%; padding:12px; margin-top:4px; border:none; border-radius:8px; background:#e5e7eb; color:var(--text); font-weight:700; font-size:15px; cursor:pointer; }

  .lp-label { border:2px solid #000; padding:12px; font-family:Tahoma,"Segoe UI",sans-serif; color:#000; background:#fff; font-size:15px; }
  .lp-row { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
  .lp-divider { border-top:2px solid #000; margin:6px 0; }
  .lp-divider.thin { border-top-width:1px; }
  .lp-addr { font-size:12px; margin:2px 0 6px; }
  .lp-line { margin:4px 0; }
  .lp-line b { font-size:19px; }
  .lp-note { margin:4px 0; }
  .lp-dose { font-weight:700; font-size:17px; margin:6px 0 2px; }
  .lp-dose2 { font-weight:700; font-size:15px; margin-bottom:6px; }
  .lp-extra { font-weight:700; font-size:14px; margin-bottom:6px; }
  .lp-warn { font-size:12px; margin:6px 0; }
  .lp-bottom { font-size:11px; color:#333; text-align:right; }
  .lp-blank { display:inline-block; border-bottom:1px solid #000; width:70px; }

  #submit-bar { position:fixed; bottom:0; left:0; right:0; background:#fff; border-top:1px solid var(--border); padding:12px 16px; z-index:20; box-shadow:0 -4px 12px rgba(0,0,0,0.06); }
  #submit-btn { width:100%; padding:16px; font-size:18px; font-weight:700; color:#fff; background:var(--primary); border:none; border-radius:12px; cursor:pointer; }
  #submit-btn:disabled { opacity:.5; }
  #toast { position:fixed; left:50%; bottom:90px; transform:translateX(-50%) translateY(20px); background:#1a1a2e; color:#fff; padding:12px 20px; border-radius:10px; font-size:14px; z-index:100; opacity:0; pointer-events:none; transition:all .25s ease; max-width:90vw; text-align:center; }
  #toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
</style>
</head>
<body>

<div id="staff-screen">
  <div class="staff-box">
    <h2>💊 คิวพิมพ์ฉลากยา</h2>
    <p>แตะชื่อของคุณเพื่อเข้าใช้งาน</p>
    <div id="staff-grid" class="staff-grid"></div>
    <div class="staff-add-row">
      <input type="text" id="staff-add-input" placeholder="เพิ่มชื่อพนักงานใหม่">
      <button onclick="addStaff()">+ เพิ่ม</button>
    </div>
    <button class="staff-edit-toggle" onclick="toggleStaffEdit()" id="staff-edit-toggle-btn">แก้ไขรายชื่อ (ลบพนักงาน)</button>
  </div>
</div>

<div id="app-screen">
  <div class="header">
    <div class="header-top">
      <h1>💊 คิวพิมพ์ฉลากยา</h1>
      <button onclick="changeStaff()">เปลี่ยนชื่อ</button>
    </div>
    <div class="staff-bar" id="staff-bar"></div>
  </div>

  <div class="section" style="padding-bottom:0;">
    <button id="fav-open-btn" onclick="openFavPicker()">⭐ โหลดจาก Favorite</button>
  </div>

  <div class="section" style="padding-top:8px;">
    <button id="patient-open-btn" onclick="openPatientDialog()">🗂 ประวัติผู้ป่วย</button>
  </div>

  <div class="section">
    <div class="section-title">ค้นหายา</div>
    <input type="text" id="search-input" placeholder="พิมพ์ชื่อยา..." autocomplete="off">
    <div id="search-results"></div>
  </div>

  <div class="section">
    <div class="selected-header">
      <div class="section-title">รายการที่เลือก (<span id="selected-count">0</span>)</div>
      <label id="allergy-toggle"><input type="checkbox" id="allergy-checkbox"> ⚠ แพ้ยา</label>
      <button id="clear-all-btn" onclick="clearAllSelected()">ล้างทั้งหมด</button>
    </div>
    <div id="selected-list"></div>
  </div>

  <div id="submit-bar">
    <button id="submit-btn" onclick="submitQueue()">📤 ส่งเข้าคิวพิมพ์</button>
  </div>
</div>

<div class="overlay" id="fav-overlay" onclick="if(event.target===this) closeFavPicker()">
  <div class="modal-card">
    <h3>⭐ เลือก Favorite</h3>
    <div id="fav-list"></div>
    <button class="modal-close-btn" onclick="closeFavPicker()">ปิด</button>
  </div>
</div>

<div class="overlay" id="patient-overlay" onclick="if(event.target===this) closePatientDialog()">
  <div class="modal-card" style="max-width:480px;">
    <h3>🗂 ประวัติผู้ป่วย</h3>
    <div style="display:flex; gap:8px; margin-bottom:8px;">
      <input type="text" id="patient-search-input" placeholder="ค้นหาชื่อ/เบอร์โทร" style="flex:1; padding:12px; font-size:15px; border:1.5px solid var(--border); border-radius:10px;">
      <button onclick="clearPatientSearch()" style="padding:0 14px; background:#555; color:#fff; border:none; border-radius:10px; font-weight:700;">✕</button>
      <button onclick="searchPatients()" style="padding:0 16px; background:var(--primary); color:#fff; border:none; border-radius:10px; font-weight:700;">ค้นหา</button>
    </div>
    <div id="patient-search-results"></div>

    <div class="patient-detail" id="patient-detail">
      <div class="patient-detail-name" id="patient-detail-name"></div>

      <div class="patient-section-title">ประวัติแพ้ยา</div>
      <textarea id="patient-allergy-input" rows="2"></textarea>
      <button class="patient-save-btn" onclick="saveAllergy()">💾 บันทึกประวัติแพ้ยา</button>

      <div class="patient-section-title">ประวัติการซื้อทั้งหมด</div>
      <div id="patient-purchase-list" style="max-height:160px; overflow-y:auto; margin-bottom:14px;"></div>

      <div class="doc-header-row">
        <div class="patient-section-title" style="margin-bottom:0;">เอกสารประกอบ</div>
        <button class="doc-upload-btn" onclick="document.getElementById('patient-upload-input').click()">📷 Upload รูป</button>
      </div>
      <input type="file" id="patient-upload-input" accept="image/*" style="display:none;" onchange="onPatientFileSelected(this)">
      <div class="upload-preview-box" id="upload-preview-box" style="display:none;">
        <img id="upload-preview-img">
        <textarea id="patient-upload-note" rows="2" placeholder="หมายเหตุ (ไม่บังคับ)"></textarea>
        <div class="upload-btn-row">
          <button class="upload-cancel-btn" onclick="cancelUpload()">ยกเลิก</button>
          <button class="upload-save-btn" onclick="confirmUpload()">💾 บันทึก</button>
        </div>
      </div>
      <div id="patient-doc-list" style="margin-top:8px;"></div>
    </div>

    <button class="modal-close-btn" onclick="closePatientDialog()">ปิด</button>
  </div>
</div>

<div class="overlay" id="lp-overlay" onclick="if(event.target===this) closePreview()">
  <div class="modal-card">
    <div class="lp-label" id="lp-content"></div>
    <button class="modal-close-btn" onclick="closePreview()">ปิด</button>
  </div>
</div>

<div class="overlay" id="confirm-overlay">
  <div class="modal-card">
    <div class="confirm-msg" id="confirm-msg"></div>
    <div class="confirm-btn-row">
      <button class="confirm-no" onclick="closeConfirm()">ยกเลิก</button>
      <button class="confirm-yes" id="confirm-yes-btn">ตกลง</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
var selected = [];
var searchTimer = null;
var staffEditMode = false;
var SETTINGS = null;
var currentStaff = '';

function escHtml(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function showToast(msg) {
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(function() { t.classList.remove('show'); }, 2600);
}

// ── Staff picker ──────────────────────────────────────────────
function showStaffScreen() {
  document.getElementById('staff-screen').classList.add('show');
  document.getElementById('app-screen').classList.remove('show');
  loadStaffGrid();
}
function showAppScreen(name) {
  currentStaff = name;
  document.getElementById('staff-screen').classList.remove('show');
  document.getElementById('app-screen').classList.add('show');
  document.getElementById('staff-bar').textContent = 'พนักงาน: ' + name;
}

function loadStaffGrid() {
  fetch('/api/staff').then(function(r) { return r.json(); }).then(function(list) {
    renderStaffGrid(list || []);
  }).catch(function() { renderStaffGrid([]); });
}

function renderStaffGrid(list) {
  window._staffList = list;
  document.getElementById('staff-grid').innerHTML = list.map(function(s) {
    return '<button class="staff-btn" onclick="pickStaff(' + escHtml(JSON.stringify(s.name)) + ')">'
      + escHtml(s.name)
      + '<span class="staff-del" onclick="event.stopPropagation(); removeStaff(' + s.id + ', ' + escHtml(JSON.stringify(s.name)) + ')">✕</span>'
      + '</button>';
  }).join('');
  document.getElementById('staff-grid').classList.toggle('staff-edit', staffEditMode);
}

function toggleStaffEdit() {
  staffEditMode = !staffEditMode;
  document.getElementById('staff-grid').classList.toggle('staff-edit', staffEditMode);
  var toggleBtn = document.getElementById('staff-edit-toggle-btn');
  toggleBtn.textContent = staffEditMode ? '✓ เสร็จแล้ว' : '✏️ แก้ไขรายชื่อ (ลบพนักงาน)';
  toggleBtn.classList.toggle('active', staffEditMode);
}

function addStaff() {
  var input = document.getElementById('staff-add-input');
  var name = input.value.trim();
  if (!name) return;
  fetch('/api/staff_add', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name}) })
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j.ok) { showToast(j.message || 'เพิ่มไม่สำเร็จ'); return; }
      input.value = '';
      loadStaffGrid();
    });
}

function removeStaff(id, name) {
  showConfirm('ลบ "' + name + '" ใช่ไหม?', function() {
    fetch('/api/staff_delete', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: id}) })
      .then(function(r) { return r.json(); })
      .then(function() { loadStaffGrid(); });
  });
}

// Custom in-page confirm instead of the native confirm() - some mobile
// in-app browsers (LINE, Facebook, etc.) handle window.confirm() poorly,
// occasionally leaving the page looking unresponsive after it's dismissed.
function showConfirm(msg, onYes) {
  document.getElementById('confirm-msg').textContent = msg;
  var yesBtn = document.getElementById('confirm-yes-btn');
  yesBtn.onclick = function() { closeConfirm(); onYes(); };
  document.getElementById('confirm-overlay').classList.add('show');
}
function closeConfirm() {
  document.getElementById('confirm-overlay').classList.remove('show');
}

function pickStaff(name) {
  localStorage.setItem('lp_staff_name', name);
  showAppScreen(name);
}

function changeStaff() {
  localStorage.removeItem('lp_staff_name');
  showStaffScreen();
}

// ── Search ────────────────────────────────────────────────────
document.getElementById('search-input').addEventListener('input', function(e) {
  var q = e.target.value;
  clearTimeout(searchTimer);
  if (q.trim().length < 1) { document.getElementById('search-results').innerHTML = ''; return; }
  searchTimer = setTimeout(function() { runSearch(q.trim()); }, 300);
});

function runSearch(q) {
  fetch('/api/search?q=' + encodeURIComponent(q))
    .then(function(r) { return r.json(); })
    .then(function(list) { renderResults(list || []); })
    .catch(function() { showToast('ค้นหาไม่สำเร็จ'); });
}

function renderResults(list) {
  var el = document.getElementById('search-results');
  if (!list.length) { el.innerHTML = '<div class="empty-hint">ไม่พบยาที่ค้นหา</div>'; return; }
  el.innerHTML = list.map(function(item, i) {
    var dotClass = item.hasInfo ? 'green' : 'red';
    var hint = item.hasInfo ? 'มีข้อมูลวิธีใช้แล้ว' : 'ยังไม่มีข้อมูลวิธีใช้';
    return '<div class="result-item" onclick="pickResult(' + i + ')">'
      + '<span class="dot ' + dotClass + '"></span>'
      + '<span class="result-name">' + escHtml(item.name) + '<div class="result-hint">' + hint + '</div></span>'
      + '</div>';
  }).join('');
  window._lastResults = list;
}

function pickResult(i) {
  var item = window._lastResults[i];
  if (!item) return;
  selected.push(item);
  renderSelected();
  document.getElementById('search-input').value = '';
  document.getElementById('search-results').innerHTML = '';
  showToast('เพิ่ม "' + item.name + '" แล้ว');
}

// ── Favorites ─────────────────────────────────────────────────
function openFavPicker() {
  document.getElementById('fav-list').innerHTML = '<div class="empty-hint">กำลังโหลด...</div>';
  document.getElementById('fav-overlay').classList.add('show');
  fetch('/api/favorites')
    .then(function(r) { return r.json(); })
    .then(function(list) { renderFavList(list || []); })
    .catch(function() { document.getElementById('fav-list').innerHTML = '<div class="empty-hint">โหลดไม่สำเร็จ</div>'; });
}
function closeFavPicker() { document.getElementById('fav-overlay').classList.remove('show'); }

function renderFavList(list) {
  var el = document.getElementById('fav-list');
  if (!list.length) { el.innerHTML = '<div class="empty-hint">ยังไม่มี Favorite</div>'; return; }
  el.innerHTML = list.map(function(f, i) {
    return '<div class="fav-item" onclick="pickFavorite(' + i + ')">'
      + '<span class="fav-name">' + escHtml(f.name) + '</span>'
      + '<span class="fav-count">' + f.count + ' รายการ</span>'
      + '</div>';
  }).join('');
  window._lastFavList = list;
}

function pickFavorite(i) {
  var fav = window._lastFavList[i];
  if (!fav) return;
  fetch('/api/favorites?name=' + encodeURIComponent(fav.name))
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j.ok) { showToast(j.message || 'โหลด Favorite ไม่สำเร็จ'); return; }
      (j.drugs || []).forEach(function(d) {
        selected.push({
          idproduct: d.idproduct,
          hasInfo: d.status ? d.status === 'db' : true,
          drug1: d.drug1 || '', drug2: d.drug2 || '', note: d.note || '',
          qty: d.qty || '', unit: d.unit || 'เม็ด', per_day: d.per_day || '',
          every_hr: d.every_hr || '', meal: d.meal || '',
          times: Array.isArray(d.times) ? d.times : [],
          extra_labels: Array.isArray(d.extra_labels) ? d.extra_labels : [],
          usage_mode: d.usage_mode || 'oral',
        });
      });
      renderSelected();
      closeFavPicker();
      showToast('เพิ่ม Favorite "' + fav.name + '" แล้ว (' + (j.drugs || []).length + ' รายการ)');
    })
    .catch(function() { showToast('เชื่อมต่อ server ไม่ได้'); });
}

// ── Patient profile (ประวัติผู้ป่วย) ─────────────────────────────
var currentPatientId = null;

function openPatientDialog() {
  document.getElementById('patient-search-input').value = '';
  document.getElementById('patient-search-results').innerHTML = '';
  document.getElementById('patient-detail').style.display = 'none';
  cancelUpload();
  currentPatientId = null;
  document.getElementById('patient-overlay').classList.add('show');
}
function closePatientDialog() { document.getElementById('patient-overlay').classList.remove('show'); }

function clearPatientSearch() {
  document.getElementById('patient-search-input').value = '';
  document.getElementById('patient-search-results').innerHTML = '';
}

function searchPatients() {
  var q = document.getElementById('patient-search-input').value.trim();
  if (!q) return;
  fetch('/api/patients?q=' + encodeURIComponent(q))
    .then(function(r) { return r.json(); })
    .then(function(list) { renderPatientResults(list || [], q); })
    .catch(function() { showToast('เชื่อมต่อ server ไม่ได้'); });
}

function renderPatientResults(list, term) {
  window._lastPatientResults = list;
  var el = document.getElementById('patient-search-results');
  if (!list.length) {
    el.innerHTML = '<div class="empty-hint">ไม่พบผู้ป่วย - '
      + '<span style="color:var(--primary); text-decoration:underline; cursor:pointer;" onclick="createPatient()">+ สร้างประวัติใหม่</span></div>';
    return;
  }
  el.innerHTML = list.map(function(p, i) {
    return '<div class="result-item" onclick="loadPatient(' + p.id + ')">'
      + '<span class="result-name">' + escHtml(p.name) + (p.phone ? ' - ' + escHtml(p.phone) : '') + '</span>'
      + '</div>';
  }).join('');
}

function createPatient() {
  var term = document.getElementById('patient-search-input').value.trim();
  var isPhoneLike = /^[0-9\-\s]+$/.test(term);
  var body = isPhoneLike ? { name: '', phone: term } : { name: term, phone: '' };
  fetch('/api/patient_create', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j.ok) { showToast(j.message || 'สร้างไม่สำเร็จ'); return; }
      loadPatient(j.id);
    });
}

function loadPatient(id) {
  currentPatientId = id;
  cancelUpload();
  fetch('/api/patient?id=' + id)
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j.ok) { showToast(j.message || 'โหลดไม่สำเร็จ'); return; }
      document.getElementById('patient-detail').style.display = 'block';
      document.getElementById('patient-detail-name').textContent =
        j.patient.name + (j.patient.phone ? ' (' + j.patient.phone + ')' : '');
      document.getElementById('patient-allergy-input').value = j.patient.allergy_note || '';
      renderPurchaseHistory(j.jobs || []);
      renderDocuments(j.documents || []);
    })
    .catch(function() { showToast('เชื่อมต่อ server ไม่ได้'); });
}

function renderPurchaseHistory(jobs) {
  var el = document.getElementById('patient-purchase-list');
  if (!jobs.length) { el.innerHTML = '<div class="empty-hint">ยังไม่มีประวัติการซื้อ</div>'; return; }
  el.innerHTML = jobs.map(function(j) {
    var names = j.drugs.slice(0, 3).map(function(d) { return d.drug1; }).join(', ');
    if (j.drugs.length > 3) names += ', ...';
    return '<div class="purchase-item">' + escHtml(j.printed_at.replace('T', ' ').slice(0, 16)) + ' - ' + escHtml(names) + '</div>';
  }).join('');
}

function saveAllergy() {
  if (!currentPatientId) return;
  var note = document.getElementById('patient-allergy-input').value;
  fetch('/api/patient_allergy', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ id: currentPatientId, allergy_note: note }),
  })
    .then(function(r) { return r.json(); })
    .then(function(j) { showToast(j.ok ? 'บันทึกประวัติแพ้ยาแล้ว' : (j.message || 'บันทึกไม่สำเร็จ')); });
}

function renderDocuments(docs) {
  var el = document.getElementById('patient-doc-list');
  if (!docs.length) { el.innerHTML = '<div class="empty-hint">ยังไม่มีเอกสาร</div>'; return; }
  el.innerHTML = docs.map(function(d) {
    return '<div class="doc-item">'
      + '<img class="doc-thumb" src="/patient_docs/' + currentPatientId + '/' + d.image_path + '">'
      + '<div class="doc-note">' + escHtml(d.note || '(ไม่มีหมายเหตุ)') + '</div>'
      + '<button class="doc-del-btn" onclick="deletePatientDoc(' + d.id + ')">✕</button>'
      + '</div>';
  }).join('');
}

// Selecting a file no longer uploads immediately - shows an inline preview
// + note field + explicit "บันทึก" button first, so it's obvious where the
// save step is instead of relying on a blocking native prompt() (which
// looked like there was no save button at all).
var pendingUploadFile = null;

function onPatientFileSelected(input) {
  if (!currentPatientId || !input.files || !input.files.length) return;
  pendingUploadFile = input.files[0];
  document.getElementById('patient-upload-note').value = '';
  var img = document.getElementById('upload-preview-img');
  img.src = URL.createObjectURL(pendingUploadFile);
  document.getElementById('upload-preview-box').style.display = 'block';
  input.value = '';
}

function cancelUpload() {
  pendingUploadFile = null;
  document.getElementById('upload-preview-box').style.display = 'none';
  document.getElementById('patient-upload-note').value = '';
}

function confirmUpload() {
  if (!currentPatientId || !pendingUploadFile) return;
  var note = document.getElementById('patient-upload-note').value;
  var formData = new FormData();
  formData.append('patient_id', currentPatientId);
  formData.append('note', note);
  formData.append('image', pendingUploadFile);
  fetch('/api/patient_upload', { method: 'POST', body: formData })
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j.ok) { showToast(j.message || 'อัปโหลดไม่สำเร็จ'); return; }
      showToast('อัปโหลดรูปแล้ว');
      cancelUpload();
      loadPatient(currentPatientId);
    })
    .catch(function() { showToast('เชื่อมต่อ server ไม่ได้'); });
}

function deletePatientDoc(id) {
  showConfirm('ลบเอกสารนี้ถาวรใช่ไหม?', function() {
    fetch('/api/patient_doc_delete', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ id: id }),
    })
      .then(function(r) { return r.json(); })
      .then(function() { loadPatient(currentPatientId); });
  });
}

// ── Dose text + label preview - mirrors label_gui.py build_label_image() ──
function buildDoseText(d) {
  var mode = d.usage_mode || 'oral';
  var dose, line2;
  var times = d.times || [];
  if (mode === 'topical') {
    dose = 'ทาบางๆ วันละ ' + (d.per_day || '__') + ' ครั้ง';
    line2 = times.join('  ');
    if (d.every_hr) line2 += '   ทุก ' + d.every_hr + ' ชม.';
  } else if (mode === 'drops') {
    dose = 'หยอดครั้งละ ' + (d.qty || '__') + ' หยด';
    if (d.per_day) dose += '   วันละ ' + d.per_day + ' ครั้ง';
    line2 = times.join('  ');
    if (d.every_hr) line2 += '   ทุก ' + d.every_hr + ' ชม.';
  } else {
    var unit = d.unit || 'เม็ด';
    dose = 'ทานครั้งละ ' + (d.qty || '__') + ' ' + unit;
    if (d.per_day) dose += '   วันละ ' + d.per_day + ' ครั้ง';
    line2 = d.meal || '';
    if (times.length) line2 += (line2 ? '    ' : '') + times.join('  ');
    if (d.every_hr) line2 += '   ทุก ' + d.every_hr + ' ชม.';
  }
  return { dose: dose, line2: line2 };
}

function thaiDateStr() {
  var d = new Date();
  var pad = function(n) { return n < 10 ? '0' + n : '' + n; };
  return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + (d.getFullYear() + 543);
}

function ensureSettings() {
  if (SETTINGS) return Promise.resolve(SETTINGS);
  return fetch('/api/settings').then(function(r) { return r.json(); }).then(function(s) { SETTINGS = s; return s; });
}

function previewLabel(i) {
  ensureSettings().then(function(s) {
    var d = selected[i];
    var t = buildDoseText(d);
    var addrLine = (s.address_line1 || '') + ' ' + (s.address_line2 || '');
    addrLine = addrLine.trim();
    if (s.phone) addrLine += (addrLine ? '  ' : '') + '(' + s.phone + ')';
    var extraHtml = (d.extra_labels && d.extra_labels.length)
      ? '<div class="lp-extra">' + d.extra_labels.map(function(e) { return '** ' + escHtml(e) + ' **'; }).join(' ') + '</div>'
      : '';
    var noteHtml = d.note ? '<div class="lp-note">' + escHtml(d.note) + '</div>' : '';
    var html = ''
      + '<div class="lp-row"><div><b>' + escHtml(s.company_name || '') + '</b></div><div>วันที่ ' + thaiDateStr() + '</div></div>'
      + (addrLine ? '<div class="lp-addr">' + escHtml(addrLine) + '</div>' : '')
      + '<div class="lp-divider"></div>'
      + '<div class="lp-row"><div>ชื่อผู้ป่วย <b>(ชื่อผู้ป่วย)</b></div><div>จำนวน <span class="lp-blank"></span></div></div>'
      + '<div class="lp-line">ชื่อยา <b>' + escHtml(d.drug1) + '</b></div>'
      + (d.drug2 ? '<div class="lp-line">ชื่อยาสามัญ <b>' + escHtml(d.drug2) + '</b></div>' : '')
      + noteHtml
      + '<div class="lp-dose">' + escHtml(t.dose) + '</div>'
      + (t.line2 ? '<div class="lp-dose2">' + escHtml(t.line2) + '</div>' : '')
      + extraHtml
      + '<div class="lp-divider thin"></div>'
      + '<div class="lp-row lp-warn"><div>แพ้ยา มีโรคประจำตัว ตั้งครรภ์ ให้นมบุตร โปรดแจ้งเภสัชกร</div>'
      + (s.pharmacist_names ? '<div class="lp-bottom">เภสัชกร: ' + escHtml(s.pharmacist_names) + '</div>' : '')
      + '</div>';
    document.getElementById('lp-content').innerHTML = html;
    document.getElementById('lp-overlay').classList.add('show');
  }).catch(function() { showToast('โหลดข้อมูลร้านไม่สำเร็จ'); });
}
function closePreview() { document.getElementById('lp-overlay').classList.remove('show'); }

// ── Selected list ─────────────────────────────────────────────
function renderSelected() {
  document.getElementById('selected-count').textContent = selected.length;
  var el = document.getElementById('selected-list');
  if (!selected.length) { el.innerHTML = '<div class="empty-hint">ยังไม่ได้เลือกยา - ค้นหาแล้วแตะเพื่อเพิ่ม</div>'; return; }
  el.innerHTML = selected.map(function(d, i) {
    var infoClass = d.hasInfo ? 'has-info' : 'no-info';
    return '<div class="selected-item ' + infoClass + '">'
      + '<button class="preview-btn" onclick="previewLabel(' + i + ')" title="พรีวิวฉลาก">👁</button>'
      + '<button class="remove-btn" onclick="removeSelected(' + i + ')" title="ลบ">✕</button>'
      + '<div class="drug-name">' + escHtml(d.drug1) + (d.drug2 ? ' <span style="font-weight:400;color:var(--muted);font-size:13px;">(' + escHtml(d.drug2) + ')</span>' : '') + '</div>'
      + '<div class="dose-preview">' + escHtml(buildDoseText(d).dose) + '</div>'
      + '</div>';
  }).join('');
}

function removeSelected(i) { selected.splice(i, 1); renderSelected(); }

function clearAllSelected() {
  if (!selected.length) return;
  showConfirm('ล้างรายการที่เลือกทั้งหมด (' + selected.length + ' รายการ) ใช่ไหม?', function() {
    selected = [];
    renderSelected();
  });
}

// ── Submit ────────────────────────────────────────────────────
function submitQueue() {
  if (!selected.length) { showToast('กรุณาเลือกยาอย่างน้อย 1 รายการ'); return; }
  var btn = document.getElementById('submit-btn');
  btn.disabled = true;
  var body = {
    patient_name: currentStaff,
    has_allergy: document.getElementById('allergy-checkbox').checked,
    drugs: selected.map(function(d) {
      return {
        idproduct: d.idproduct, drug1: d.drug1, drug2: d.drug2, note: d.note,
        qty: d.qty, unit: d.unit, per_day: d.per_day, every_hr: d.every_hr, meal: d.meal,
        times: d.times, extra_labels: d.extra_labels, usage_mode: d.usage_mode, print_qty: 1,
        status: d.hasInfo ? 'db' : 'missing',
      };
    })
  };
  fetch('/api/submit', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })
    .then(function(r) { return r.json(); })
    .then(function(j) {
      btn.disabled = false;
      if (!j.ok) { showToast(j.message || 'ส่งไม่สำเร็จ'); return; }
      selected = [];
      document.getElementById('allergy-checkbox').checked = false;
      renderSelected();
      showToast('✅ ส่งไปที่คิวพิมพ์ฉลากแล้ว');
    })
    .catch(function() { btn.disabled = false; showToast('เชื่อมต่อ server ไม่ได้'); });
}

// ── Init ──────────────────────────────────────────────────────
renderSelected();
var savedStaff = localStorage.getItem('lp_staff_name');
if (savedStaff) { showAppScreen(savedStaff); } else { showStaffScreen(); }
</script>
</body>
</html>
"""
