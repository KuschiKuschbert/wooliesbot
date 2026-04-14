import http.server
import socketserver
import json
import threading
from keep_sync import run_keep_sync

PORT = 5001

class LocalBotHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With')
        self.end_headers()

    def do_GET(self):
        if self.path == '/sync':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            # Run sync in a separate thread so we can reply to the browser immediately
            threading.Thread(target=run_keep_sync, daemon=True).start()
            
            response = {"status": "success", "message": "Sync started in background"}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    # Allow restarting the server immediately without 'Address already in use' error
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), LocalBotHandler) as httpd:
        print(f"✅ WooliesBot local API running at http://localhost:{PORT}")
        print("   Dashboard can now trigger Google Keep sync directly!")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
