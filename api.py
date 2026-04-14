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

    def do_POST(self):
        if self.path == '/update_stock':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            params = json.loads(post_data)
            
            item_name = params.get('name')
            new_stock = params.get('stock') # 'low', 'medium', 'full'
            
            try:
                with open('docs/data.json', 'r+') as f:
                    items = json.load(f)
                    for item in items:
                        if item['name'] == item_name:
                            item['stock'] = new_stock
                            if new_stock == 'full':
                                item['last_purchased'] = datetime.datetime.now().strftime("%Y-%m-%d")
                            break
                    f.seek(0)
                    json.dump(items, f, indent=4)
                    f.truncate()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_response(404)
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
        elif self.path == '/ping':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

# Import added for update_stock logic
import datetime

def run_server():
    # Allow restarting the server immediately without 'Address already in use' error
    socketserver.TCPServer.allow_reuse_address = True
    
    while True:
        try:
            with socketserver.TCPServer(("", PORT), LocalBotHandler) as httpd:
                print(f"✅ WooliesBot local API running at http://localhost:{PORT}")
                httpd.serve_forever()
        except Exception as e:
            print(f"⚠️ API Server encountered an error: {e}. Restarting in 5s...")
            import time
            time.sleep(5)

if __name__ == "__main__":
    run_server()
