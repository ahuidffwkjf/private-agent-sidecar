"""TLS-only Relay entrypoint for an operator-managed public experiment server."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import signal
import ssl
import stat
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def private_file(path):
    path = Path(path)
    info = path.lstat()
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode):
        raise ValueError('credential must be an absolute regular file')
    if info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('credential must be owned by the service user with mode 0600')
    return path


def load_config(path):
    value = json.loads(Path(path).read_text('utf-8'))
    required = {'host', 'port', 'database', 'tls_cert', 'tls_key', 'token_file', 'rate_limit_per_minute'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('unexpected or missing Relay configuration fields')
    ipaddress.IPv4Address(value['host'])
    for key, maximum in [('port', 65535), ('rate_limit_per_minute', 10000)]:
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ValueError('invalid ' + key)
    for key in ('database', 'tls_cert', 'tls_key', 'token_file'):
        if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
            raise ValueError(key + ' must be an absolute path')
    token_path = private_file(value['token_file'])
    if token_path.stat().st_size > 4096:
        raise ValueError('Relay token file is too large')
    token = token_path.read_text('ascii').strip()
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Relay requires a nonempty strong access token')
    private_file(value['tls_key'])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(value['tls_cert'], value['tls_key'], password=lambda: '')
    return value, token, context


class BoundedRelayServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, context, max_clients=64):
        self.slots = threading.BoundedSemaphore(max_clients)
        super().__init__(address, handler)
        # Handshakes happen in request threads, not the single accept loop.
        self.socket = context.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(15)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Do not log attacker-controlled request bodies or credentials.
        print('Relay request failed', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    value, token, context = load_config(args.config)
    if args.check:
        print(json.dumps({'configuration': 'valid', 'tls_required': True, 'port': value['port']}))
        return
    from agent_sidecar.relay import make_handler
    from agent_sidecar.store import RelayStore
    store = RelayStore(Path(value['database']))
    handler = make_handler(store, token, value['rate_limit_per_minute'])
    server = BoundedRelayServer((value['host'], value['port']), handler, context)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(json.dumps({'service': 'sidecar-ecs-relay', 'tls': True, 'port': value['port']}), flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        store.close()


if __name__ == '__main__':
    main()
