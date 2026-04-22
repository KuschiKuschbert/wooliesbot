from __future__ import annotations

import contextlib
import http.server
import socket
import socketserver
import threading
from pathlib import Path


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        pass


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def serve_docs(docs_dir: Path, port: int):
    class Handler(_QuietHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(docs_dir), **kwargs)

    httpd = _ReusableTCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
