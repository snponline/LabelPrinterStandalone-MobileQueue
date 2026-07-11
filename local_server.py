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
  #clear-all-btn { background:none; border:none; color:var(--danger); font-size:13px; font-weight:700; cursor:pointer; padding:4px 0 8px; }
  #clear-all-btn:active { opacity:.6; }

  #fav-open-btn { width:100%; padding:12px; font-size:15px; font-weight:700; color:var(--primary); background:#fff; border:1.5px solid var(--primary); border-radius:10px; cursor:pointer; }
  #fav-open-btn:active { background:var(--bg); }

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

  <div class="section">
    <div class="section-title">ค้นหายา</div>
    <input type="text" id="search-input" placeholder="พิมพ์ชื่อยา..." autocomplete="off">
    <div id="search-results"></div>
  </div>

  <div class="section">
    <div class="selected-header">
      <div class="section-title">รายการที่เลือก (<span id="selected-count">0</span>)</div>
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
