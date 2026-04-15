import http.server
import socketserver
import json
import datetime
import threading
import logging
import os
from logging.handlers import RotatingFileHandler

from keep_sync import run_keep_sync

PORT = 5001

# ── Logging ──────────────────────────────────────────────────────────────────
_log_format = '%(asctime)s - %(levelname)s - %(message)s'
_handler = RotatingFileHandler("logs/api.log", maxBytes=2*1024*1024, backupCount=2, encoding="utf-8")
_handler.setFormatter(logging.Formatter(_log_format))
_stream = logging.StreamHandler()
_stream.setFormatter(logging.Formatter(_log_format))
logging.basicConfig(level=logging.INFO, handlers=[_handler, _stream])

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")
_data_lock = threading.Lock()


def _load_data():
    """Load data.json and return (raw_wrapper_or_list, items_list)."""
    with open(DATA_FILE, "r") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        return raw, raw.get("items", [])
    return raw, raw  # legacy plain-list format


def _save_data(raw, items):
    """Write data.json back, preserving the {last_updated, items} wrapper."""
    if isinstance(raw, dict):
        raw["items"] = items
        payload = raw
    else:
        payload = items
    with open(DATA_FILE, "w") as f:
        json.dump(payload, f, indent=2)


class LocalBotHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.debug("HTTP %s", format % args)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Requested-With')
        self.end_headers()

    def do_POST(self):
        if self.path == '/update_stock':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                params = json.loads(post_data)

                item_name = params.get('name')
                new_stock  = params.get('stock')  # 'low', 'medium', 'full'
                new_target = params.get('target')  # optional float

                if not item_name or not new_stock:
                    self.send_response(400)
                    self.end_headers()
                    return

                with _data_lock:
                    raw, items = _load_data()
                    updated = False
                    for item in items:
                        if item.get('name') == item_name:
                            item['stock'] = new_stock
                            if new_stock == 'full':
                                item['last_purchased'] = datetime.datetime.now().strftime("%Y-%m-%d")
                            if new_target is not None:
                                item['target'] = float(new_target)
                            updated = True
                            break
                    if updated:
                        _save_data(raw, items)

                self.send_response(200 if updated else 404)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success" if updated else "not_found"}).encode())
            except Exception as e:
                logging.error(f"update_stock error: {e}")
                self.send_error(500, str(e))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "service": "WooliesBot Bridge"}).encode())

        elif self.path == '/sync':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            # Run sync in a separate thread so we can reply immediately
            threading.Thread(target=run_keep_sync, daemon=True).start()
            self.wfile.write(json.dumps({"status": "success", "message": "Sync started in background"}).encode())

        elif self.path == '/ping':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())

        else:
            self.send_response(404)
            self.end_headers()


def run_server():
    os.makedirs("logs", exist_ok=True)
    # Allow restarting the server immediately without 'Address already in use' error
    socketserver.TCPServer.allow_reuse_address = True

    while True:
        try:
            with socketserver.TCPServer(("", PORT), LocalBotHandler) as httpd:
                logging.info(f"✅ WooliesBot local API running at http://localhost:{PORT}")
                httpd.serve_forever()
        except Exception as e:
            logging.error(f"API Server error: {e}. Restarting in 5s...")
            import time
            time.sleep(5)


if __name__ == "__main__":
    run_server()
