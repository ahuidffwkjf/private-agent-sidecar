from __future__ import annotations

import json
import ssl
from pathlib import Path
from typing import Optional
from http.server import BaseHTTPRequestHandler
from typing import Any


class JsonHandler(BaseHTTPRequestHandler):
    server_version = "PrivateAgentSidecar/0.1"

    def read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2 * 1024 * 1024:
            raise ValueError("request body is too large")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def send_json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))



def enable_tls(server, certfile: Optional[Path] = None, keyfile: Optional[Path] = None):
    """Wrap a ThreadingHTTPServer socket with an operator-provided TLS cert."""
    if certfile is None and keyfile is None:
        return server
    if certfile is None or keyfile is None:
        raise ValueError("tls certfile and keyfile must be provided together")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certfile), str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
