"""Owned local desktop runtime. Credentials travel over stdin, never argv or URLs."""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import secrets
import signal
import socketserver
import sys
import threading
from http.cookies import CookieError, SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .relay import make_handler as relay_handler
from .service import Sidecar, make_handler as sidecar_handler
from .store import RelayStore

COOKIE = 'sidecar_desktop_session'


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        if self.server_address[0] != '127.0.0.1':
            raise ValueError('desktop services must bind IPv4 loopback')
        # HTTPServer resolves a hostname here, which can block on broken DNS.
        # These owned local services use only their numeric loopback address.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def desktop_handler(sidecar, token, origin):
    base = sidecar_handler(sidecar)

    class Handler(base):
        def authorized(self):
            if self.headers.get('Host') != urlparse(origin).netloc:
                self.send_json(403, {'error': 'invalid_desktop_host'})
                return False
            source = self.headers.get('Origin')
            if source and source != origin:
                self.send_json(403, {'error': 'invalid_desktop_origin'})
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.send_json(403, {'error': 'cross_site_desktop_request'})
                return False
            # Direct peers still pass the original signed/encrypted scope checks.
            if (self.command, urlparse(self.path).path) in {
                ('GET', '/v1/direct/healthz'), ('POST', '/v1/direct/messages')
            }:
                return True
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                supplied = cookies.get(COOKIE)
                valid = supplied is not None and hmac.compare_digest(supplied.value, token)
            except (CookieError, ValueError, TypeError):
                valid = False
            if not valid:
                self.send_json(401, {'error': 'desktop_session_required'})
            return valid

        def do_GET(self):
            if self.authorized():
                super().do_GET()

        def do_POST(self):
            if self.authorized():
                super().do_POST()

        def do_PUT(self):
            if self.authorized():
                super().do_PUT()

        def do_DELETE(self):
            if self.authorized():
                super().do_DELETE()

        def log_message(self, fmt, *args):
            # Do not copy headers, cookies or message bodies to the desktop logs.
            pass

    return Handler


class DesktopRuntime:
    def __init__(self, data_dir: Path, token: str):
        if not isinstance(token, str) or len(token) < 32 or not token.isascii() or not token.isalnum():
            raise ValueError('desktop session must be an ASCII alphanumeric token of at least 32 characters')
        # Owned services must never send local session/content requests through
        # a system proxy. Preserve existing bypasses and leave external routing.
        bypass = os.environ.get('no_proxy', os.environ.get('NO_PROXY', ''))
        values = [value for value in bypass.split(',') if value]
        for host in ('127.0.0.1', 'localhost', '::1'):
            if host not in values:
                values.append(host)
        os.environ['no_proxy'] = os.environ['NO_PROXY'] = ','.join(values)
        self.root = data_dir
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.token = token
        self.servers = []
        self.threads = []
        self.stores = []
        self.sidecar = None
        self.url = None

    def start(self):
        try:
            relay_store = RelayStore(self.root / 'relay.db')
            self.stores.append(relay_store)
            relay_token = secrets.token_hex(32)
            relay = LoopbackHTTPServer(('127.0.0.1', 0), relay_handler(relay_store, relay_token))
            self.servers.append(relay)
            self._start_server(relay)
            # Reserve the UI port before publishing this node's direct endpoint.
            server = LoopbackHTTPServer(('127.0.0.1', 0), sidecar_handler(None))
            self.servers.append(server)
            self.url = f'http://127.0.0.1:{server.server_port}'
            self.sidecar = Sidecar(self.root / 'node', f'http://127.0.0.1:{relay.server_port}', relay_token, self.url, hardware_profile='basic')
            self.stores.append(self.sidecar.store)
            server.RequestHandlerClass = desktop_handler(self.sidecar, self.token, self.url)
            self._start_server(server)
            self.sidecar.bounties.start()
            self.sidecar.toy.start()
            return self.url
        except Exception:
            self.close()
            raise

    def _start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        thread.start()
        self.threads.append((server, thread))

    def close(self):
        if self.sidecar:
            self.sidecar.bounties.stop()
            self.sidecar.toy.stop()
        for server, thread in reversed(self.threads):
            server.shutdown()
            thread.join(timeout=5)
        for server in reversed(self.servers):
            server.server_close()
        for store in reversed(self.stores):
            store.close()
        self.threads.clear()
        self.servers.clear()
        self.stores.clear()


def main():
    # stdout is a one-line readiness protocol; diagnostics go to stderr.
    # Electron writes UTF-8 even on Windows with a non-UTF-8 system code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    output = sys.stdout
    config = json.loads(sys.stdin.readline(65537))
    root = Path(config['data_dir'])
    if not root.is_absolute():
        raise ValueError('data_dir must be absolute')
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())

    def monitor_parent():
        for line in sys.stdin:
            if line.strip() == 'shutdown':
                break
        stopped.set()

    with contextlib.redirect_stdout(sys.stderr):
        if config.get('mode') == 'toy':
            from .toy_desktop import ToyDesktopRuntime
            runtime = ToyDesktopRuntime(root, config['token'])
        else:
            runtime = DesktopRuntime(root, config['token'])
        try:
            url = runtime.start()
            ready = {'url': url, 'pid': os.getpid()}
            if config.get('mode') == 'toy':
                ready['users'] = runtime.users
                ready['mode'] = 'toy'
            output.write(json.dumps(ready) + '\n')
            output.flush()
            threading.Thread(target=monitor_parent, daemon=True).start()
            stopped.wait()
        finally:
            runtime.close()


if __name__ == '__main__':
    main()
