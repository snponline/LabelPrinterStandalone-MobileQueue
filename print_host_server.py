"""Print host for LabelPrinterStandalone_MobileQueue — PC with the label
printer. Accepts pre-rendered PNG labels (base64) over LAN HTTP and prints
via Windows GDI (win32print). No SQLite / queue / business logic.

Same pattern as PharmacyPOS / HOPE label_printer.

  python print_host_server.py --port 8970

Or started automatically when role.json has print_host=true (see machine_role.py).
"""
import argparse
import base64
import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PIL import Image, ImageWin

DEFAULT_PORT = 8970
PORT_RANGE = range(8970, 8980)

_server = None
_server_addr = (None, None)  # (ip, port) once running


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def print_image(img, printer_name):
    """1:1 pixel draw — do NOT stretch to VERTRES (thermal continuous roll
    reports huge height; stretching yields meters of blurry paper)."""
    import win32print  # noqa: F401
    import win32ui

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer_name)
    hdc.StartDoc("LabelPrinter print host")
    hdc.StartPage()
    dib = ImageWin.Dib(img)
    dib.draw(hdc.GetHandleOutput(), (0, 0, img.width, img.height))
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()


def list_printers():
    import win32print

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    try:
        return sorted(p[2] for p in win32print.EnumPrinters(flags))
    except Exception:
        return []


def get_printable_width(printer_name):
    import win32ui

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer_name)
    try:
        return hdc.GetDeviceCaps(8)  # HORZRES
    finally:
        hdc.DeleteDC()


def _live_station_meta():
    """Read station/printer from settings.json so /ping updates without restart."""
    sid = Handler.station_id or ""
    sname = Handler.station_name or ""
    dprn = Handler.default_printer or ""
    try:
        import app_settings
        s = app_settings.load_settings()
        if "station_id" in s and str(s.get("station_id") or "").strip() != "":
            sid = str(s.get("station_id") or "").strip()
        if "station_name" in s:
            sname = str(s.get("station_name") or "").strip()
        if "printer_name" in s and str(s.get("printer_name") or "").strip():
            dprn = str(s.get("printer_name") or "").strip()
    except Exception:
        pass
    return {"station_id": sid, "station_name": sname, "default_printer": dprn}


class Handler(BaseHTTPRequestHandler):
    # Fallback defaults set by start_server (overridden live by settings file)
    station_id = ""
    station_name = ""
    default_printer = ""

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        meta = _live_station_meta()
        if parsed.path == "/ping":
            self._send_json({
                "ok": True,
                "station_id": meta["station_id"],
                "station_name": meta["station_name"],
                "default_printer": meta["default_printer"],
            })
        elif parsed.path == "/printers":
            self._send_json({
                "ok": True,
                "printers": list_printers(),
                "default_printer": meta["default_printer"],
                "station_id": meta["station_id"],
                "station_name": meta["station_name"],
            })
        elif parsed.path == "/printable_width":
            printer_name = qs.get("printer_name", [""])[0]
            if not printer_name:
                self._send_json({"ok": False, "message": "ขาด printer_name"}, 400)
                return
            try:
                self._send_json({"ok": True, "width": get_printable_width(printer_name)})
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/print":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except json.JSONDecodeError:
                self._send_json({"ok": False, "message": "JSON ไม่ถูกต้อง"}, 400)
                return
            meta = _live_station_meta()
            printer_name = (body.get("printer_name") or meta["default_printer"] or "").strip()
            image_base64 = body.get("image_base64")
            if not printer_name or not image_base64:
                self._send_json({"ok": False, "message": "ขาด printer_name หรือ image_base64"}, 400)
                return
            try:
                raw = base64.b64decode(image_base64)
                img = Image.open(io.BytesIO(raw))
                copies = int(body.get("copies") or 1)
                copies = max(1, min(copies, 20))
                for _ in range(copies):
                    print_image(img, printer_name)
                self._send_json({"ok": True, "printed": copies, "printer_name": printer_name})
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[print_host] {self.address_string()} - {format % args}")


def start_server(port=None, station_id="", station_name="", default_printer=""):
    """Start daemon thread. Returns (ip, port) or (None, None)."""
    global _server, _server_addr
    Handler.station_id = station_id or ""
    Handler.station_name = station_name or ""
    Handler.default_printer = default_printer or ""

    ports = [int(port)] if port else list(PORT_RANGE)
    for p in ports:
        try:
            _server = ThreadingHTTPServer(("0.0.0.0", p), Handler)
            break
        except OSError:
            _server = None
            continue
    else:
        _server_addr = (None, None)
        return None, None

    ip = get_lan_ip()
    actual_port = _server.server_address[1]
    _server_addr = (ip, actual_port)
    t = threading.Thread(target=_server.serve_forever, daemon=True)
    t.start()
    print(f"[print_host] listening on http://{ip}:{actual_port}")
    printers = list_printers()
    print(f"[print_host] printers: {', '.join(printers) if printers else '(none)'}")
    return ip, actual_port


def get_addr():
    return _server_addr


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--station-id", default="")
    parser.add_argument("--station-name", default="")
    parser.add_argument("--printer", default="")
    args = parser.parse_args()
    ip, port = start_server(
        port=args.port,
        station_id=args.station_id,
        station_name=args.station_name,
        default_printer=args.printer,
    )
    if not port:
        print("bind failed")
        return 1
    print(f"Print host ready: http://{ip}:{port}/ping")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
