"""Persistent, explicitly authorized Tailcat routes and supervised connections."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import ssl
import subprocess
import sys
import threading
import time

from .crypto import canonical, verify_signature


def load_network_config(filename):
    path = private_file(filename)
    if path.stat().st_size > 65536:
        raise ValueError('network configuration too large')
    config = json.loads(path.read_text('utf-8'))
    if not isinstance(config, dict) or set(config) - {'tailcat_bin', 'key_file', 'client_keys', 'address_file', 'peer_port', 'tls_cert', 'tls_key', 'max_connections'}:
        raise ValueError('invalid network configuration fields')
    for name in ('key_file', 'tls_cert', 'tls_key'):
        private_file(config[name])
    clients = config.get('client_keys', {})
    if not isinstance(clients, dict) or len(clients) > 128:
        raise ValueError('client_keys must map at most 128 peer IDs to private key files')
    key_paths = {str(Path(config['key_file']).resolve())}
    for peer_id, filename in clients.items():
        if not re.fullmatch(r'agent:[0-9a-f]{32}', peer_id):
            raise ValueError('invalid client key peer ID')
        resolved = str(private_file(filename).resolve())
        if resolved in key_paths:
            raise ValueError('every serve/forward process requires its own persistent identity key')
        key_paths.add(resolved)
    address = Path(config['address_file'])
    if not address.is_absolute() or address.is_symlink() or not address.parent.is_dir():
        raise ValueError('address_file requires existing private directory')
    if os.name != 'nt' and (address.parent.stat().st_mode & 0o077 or address.parent.stat().st_uid != os.getuid()):
        raise ValueError('address directory requires owner-only permissions')
    if address.exists():
        private_file(address)
    if type(config['peer_port']) is not int or not 1 <= config['peer_port'] <= 65535:
        raise ValueError('peer_port must be 1..65535')
    limit = config.get('max_connections', 8)
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError('max_connections must be 1..32')
    config.setdefault('tailcat_bin', 'tailcat')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['tls_cert'], config['tls_key'], password=lambda: '')
    config['tls_fingerprint'] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(config['tls_cert']).read_text())).hexdigest()
    return config


def peer_only_handler(sidecar):
    from .service import make_handler
    from urllib.parse import urlparse
    base = make_handler(sidecar)

    class Handler(base):
        def do_GET(self):
            if urlparse(self.path).path == '/v1/direct/healthz':
                return super().do_GET()
            self.send_json(404, {'error': 'peer_endpoint_only'})

        def do_POST(self):
            if urlparse(self.path).path == '/v1/direct/messages':
                return super().do_POST()
            self.send_json(404, {'error': 'peer_endpoint_only'})

        def do_PUT(self):
            self.send_json(404, {'error': 'peer_endpoint_only'})

        do_DELETE = do_PUT

        def log_message(self, fmt, *args):
            pass

    return Handler


def private_file(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('network input must be an absolute regular file')
    info = path.stat()
    if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('network input requires owner-only permissions')
    return path


class NetworkLease:
    """OS lock automatically released after a crash; never trust a stale PID file."""
    def __init__(self, path):
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        self.fd = os.open(path, flags, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b'0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise ValueError('network manager already owns this node directory') from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def validate_route(value, peer, now=None):
    now = int(time.time() if now is None else now)
    body = value.get('route', {})
    if body.get('protocol') != 'sidecar-tailcat-route/1' or body.get('agent_id') != peer.agent_id:
        raise ValueError('route identity mismatch')
    if not re.fullmatch(r'agent:[0-9a-f]{32}', body.get('audience', '')) or body['audience'] == peer.agent_id:
        raise ValueError('route requires a distinct recipient node')
    if not verify_signature(peer.signing_key, value.get('signature', ''), canonical(body)):
        raise ValueError('invalid route signature')
    for name in ('version', 'issued_at', 'expires_at', 'port'):
        if type(body.get(name)) is not int:
            raise ValueError('route numeric field invalid')
    if body['version'] < 1 or not 1 <= body['port'] <= 65535:
        raise ValueError('invalid route version or port')
    if body['issued_at'] > now + 60 or not now < body['expires_at'] <= body['issued_at'] + 30 * 86400:
        raise ValueError('expired or invalid route validity')
    if not re.fullmatch(r'tc[A-Za-z0-9._:-]{20,8192}', body.get('address', '')):
        raise ValueError('invalid Tailcat address')
    if not re.fullmatch(r'nodekey:[0-9a-f]{64}', body.get('client_key', '')):
        raise ValueError('invalid Tailcat client public key')
    if not re.fullmatch(r'[0-9a-f]{64}', body.get('tls_fingerprint', '')):
        raise ValueError('route requires TLS SHA-256 pin')
    return dict(body)


class NetworkStore:
    def __init__(self, path=None, local_store=None):
        self.local_store = local_store
        self.lock = local_store.lock if local_store else threading.RLock()
        self.db = local_store.db if local_store else sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if not local_store and os.name != 'nt':
            os.chmod(path, 0o600)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS routes(
            agent_id TEXT PRIMARY KEY, version INTEGER NOT NULL, signed_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS network_exports(version INTEGER PRIMARY KEY, signed_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox(
            message_id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL, signing_key TEXT NOT NULL,
            body TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL,
            error_code TEXT NOT NULL DEFAULT '');
        ''')

    def install_route(self, signed, peer, now=None):
        route = validate_route(signed, peer, now)
        encoded = json.dumps(signed, sort_keys=True)
        with self.lock, self.db:
            old = self.db.execute('SELECT * FROM routes WHERE agent_id=?', (peer.agent_id,)).fetchone()
            if not old and self.db.execute('SELECT count(*) FROM routes').fetchone()[0] >= 128:
                raise ValueError('route directory limit reached')
            if old and (route['version'] < old['version'] or (route['version'] == old['version'] and encoded != old['signed_json'])):
                raise ValueError('route rollback or same-version conflict')
            self.db.execute('INSERT INTO routes VALUES(?,?,?) ON CONFLICT(agent_id) DO UPDATE SET version=excluded.version,signed_json=excluded.signed_json', (peer.agent_id, route['version'], encoded))

    def record_export(self, signed):
        with self.lock, self.db:
            previous = self.db.execute('SELECT max(version) FROM network_exports').fetchone()[0] or 0
            if signed['route']['version'] <= previous:
                raise ValueError('export version must increase')
            self.db.execute('DELETE FROM network_exports')
            self.db.execute('INSERT INTO network_exports VALUES(?,?)', (signed['route']['version'], json.dumps(signed)))

    def routes(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT signed_json FROM routes')]

    def has_route(self, agent_id):
        with self.lock:
            return self.db.execute('SELECT 1 FROM routes WHERE agent_id=?', (agent_id,)).fetchone() is not None

    def enqueue(self, envelope, peer, max_pending=1000, history=None):
        body = json.dumps({'scope': 'private', 'envelope': envelope}, separators=(',', ':'))
        if len(body.encode()) > 1024 * 1024:
            raise ValueError('durable message exceeds 1 MiB')
        with self.lock, self.db:
            self.db.execute("DELETE FROM outbox WHERE status!='queued' AND expires_at<?", (time.time() - 7 * 86400,))
            if self.db.execute("SELECT count(*) FROM outbox WHERE status='queued'").fetchone()[0] >= max_pending:
                raise ValueError('durable outbox is full')
            if self.db.execute('SELECT count(*) FROM outbox').fetchone()[0] >= 10000:
                raise ValueError('durable outbox retention limit reached')
            self.db.execute('INSERT INTO outbox(message_id,recipient_id,signing_key,body,status,expires_at) VALUES(?,?,?,?,?,?)',
                            (envelope['message_id'], peer.agent_id, peer.signing_key, body, 'queued', envelope['expires_at']))
            if history is not None:
                if not self.local_store:
                    raise ValueError('history requires the local transaction')
                self.db.execute('INSERT INTO private_messages(message_id,peer_id,direction,message_type,payload_json,created_at,status) VALUES(?,?,?,?,?,?,?)',
                                (envelope['message_id'], peer.agent_id, 'outgoing', history['type'], json.dumps(history['payload']), envelope['created_at'], 'queued'))
                if getattr(self.local_store, 'research_client', None):
                    self.local_store.research_client.record('private.outgoing', history, [peer.agent_id], envelope['message_id'])

    def due(self, now):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE status='queued' AND next_attempt<=? ORDER BY next_attempt LIMIT 32", (now,))]

    def finish(self, message_id, status, code='', retry_at=0):
        with self.lock, self.db:
            self.db.execute('UPDATE outbox SET status=?,error_code=?,next_attempt=?,attempts=attempts+1 WHERE message_id=?', (status, code, retry_at, message_id))
            if self.local_store:
                self.db.execute("UPDATE private_messages SET status=? WHERE message_id=? AND direction='outgoing'", (status, message_id))

    def status(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT message_id,recipient_id,status,attempts,next_attempt,expires_at,error_code FROM outbox ORDER BY rowid DESC LIMIT 200')]

    def pending_peers(self):
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT DISTINCT recipient_id FROM outbox WHERE status='queued'")}

    def close(self):
        if not self.local_store:
            self.db.close()


class Tunnel:
    """One owned process, capped jittered restart, no raw command/log disclosure."""
    def __init__(self, command, env=None):
        self.command = command
        self.env = env
        self.child = None
        self.failures = 0
        self.next_attempt = 0
        self.started = 0
        self.last_exit = None

    def stop(self):
        child, self.child = self.child, None
        if child and child.stdin:
            try:
                child.stdin.close()
            except OSError:
                pass
        if child and child.poll() is None:
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)

    def failed(self, now):
        self.stop()
        self.failures += 1
        self.next_attempt = now + random.uniform(.5, 1) * min(60, 2 ** min(self.failures, 6))

    def tick(self, now):
        if self.child and self.child.poll() is not None:
            self.last_exit = self.child.returncode
            self.failed(now)
        if self.child is None and now >= self.next_attempt:
            try:
                runner = [sys.executable, '--tailcat-worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'agent_sidecar.tailcat_worker']
                self.child = subprocess.Popen(runner, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL, env=self.env, **({'umask': 0o077} if os.name != 'nt' else {}))
                self.child.stdin.write(json.dumps(self.command).encode('utf-8') + b'\n')
                self.child.stdin.flush()
                self.started = now
            except OSError:
                self.failed(now)


class NetworkManager:
    def __init__(self, sidecar, config):
        self.sidecar = sidecar
        self.config = config
        self.binary = config['tailcat_bin']
        self.key = private_file(config['key_file'])
        version = subprocess.run([self.binary, 'version'], capture_output=True, text=True, timeout=10)
        if version.returncode or version.stdout.strip() != 'v0.7.0':
            raise ValueError('network manager requires Tailcat v0.7.0')
        self.store = NetworkStore(local_store=sidecar.store)
        self.lock = threading.RLock()
        self.forwarders = {}
        self.server = None
        self.allowlist = None
        self.stop_event = threading.Event()
        self.thread = None
        self.workers = []
        self.last_sync = 0
        self.errors = 0
        self.limit = int(config.get('max_connections', 8))
        if not 1 <= self.limit <= 32:
            raise ValueError('max_connections must be 1..32')
        self.lease = NetworkLease(sidecar.store.data_dir / 'network.lock')

    def eligible(self, signed):
        agent_id = signed.get('route', {}).get('agent_id', '')
        peer = self.sidecar.trusted_peer(agent_id)
        if signed.get('route', {}).get('audience') != self.sidecar.identity.agent_id:
            return None
        if not peer or not self.sidecar._private_trust_ready(agent_id):
            return None
        try:
            return validate_route(signed, peer)
        except ValueError:
            return None

    def import_route(self, signed):
        agent_id = signed.get('route', {}).get('agent_id', '')
        peer = self.sidecar.trusted_peer(agent_id)
        if not peer or not self.sidecar._private_trust_ready(agent_id):
            raise ValueError('route requires existing mutual trust')
        if signed.get('route', {}).get('audience') != self.sidecar.identity.agent_id:
            raise ValueError('route is addressed to another node')
        self.store.install_route(signed, peer)
        self.reconcile()

    def export_route(self, version, recipient_id, ttl=86400):
        if type(version) is not int or version < 1 or not 60 <= ttl <= 30 * 86400:
            raise ValueError('invalid route version or lifetime')
        address = private_file(self.config['address_file']).read_text('utf-8').strip()
        if not self.sidecar.trusted_peer(recipient_id) or not self.sidecar._private_trust_ready(recipient_id):
            raise ValueError('route export requires mutual trust')
        client = private_file(self.config.get('client_keys', {})[recipient_id])
        key = subprocess.run([self.binary, f'--key={client}', 'printpub'], capture_output=True, text=True, timeout=10)
        if key.returncode:
            raise ValueError('cannot read client public key')
        now = int(time.time())
        body = {'protocol': 'sidecar-tailcat-route/1', 'agent_id': self.sidecar.identity.agent_id,
                'audience': recipient_id,
                'version': version, 'issued_at': now, 'expires_at': now + ttl,
                'address': address, 'client_key': key.stdout.strip(), 'port': self.config['peer_port'],
                'tls_fingerprint': self.config['tls_fingerprint']}
        signed = {'route': body, 'signature': self.sidecar.identity.sign(canonical(body))}
        validate_route(signed, self.sidecar.identity.public())
        self.store.record_export(signed)
        return signed

    def reconcile(self):
        routes = [r for s in self.store.routes() if (r := self.eligible(s))]
        allowed = ','.join(sorted({r['client_key'] for r in routes})) or 'none'
        now = time.monotonic()
        pending = self.store.pending_peers()
        with self.lock:
            if allowed != self.allowlist:
                if self.server:
                    self.server.stop()
                env = dict(os.environ)
                env['TAILCAT_ADDR_FILE'] = self.config['address_file']
                self.server = Tunnel([self.binary, 'serve', f'--key={self.key}', f'--allow={allowed}', str(self.config['peer_port'])], env)
                self.allowlist = allowed
            self.server.tick(now)
            valid = {r['agent_id']: r for r in routes}
            for agent_id, entry in list(self.forwarders.items()):
                if agent_id not in valid or valid[agent_id]['version'] != entry['route']['version'] or (agent_id not in pending and not entry.get('active') and now - entry['used'] > 120):
                    entry['tunnel'].stop()
                    del self.forwarders[agent_id]
                else:
                    entry['tunnel'].tick(now)

    def endpoint(self, agent_id):
        if self.stop_event.is_set():
            raise RuntimeError('network manager is stopping')
        self.reconcile()
        route = next((r for s in self.store.routes() if (r := self.eligible(s)) and r['agent_id'] == agent_id), None)
        if route is None:
            raise RuntimeError('no valid authorized Tailcat route')
        with self.lock:
            now = time.monotonic()
            entry = self.forwarders.get(agent_id)
            if not entry:
                if len(self.forwarders) >= self.limit:
                    idle = [(key, value) for key, value in self.forwarders.items() if not value.get('active')]
                    if not idle:
                        raise RuntimeError('connection limit reached')
                    key, victim = min(idle, key=lambda item: item[1]['used'])
                    victim['tunnel'].stop()
                    del self.forwarders[key]
                import socket
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                env = dict(os.environ)
                env.pop('TAILCAT_ADDR_FILE', None)
                filename = self.config.get('client_keys', {}).get(agent_id)
                if not filename:
                    raise RuntimeError('peer outbound identity is not configured')
                client = private_file(filename)
                tunnel = Tunnel([self.binary, 'forward', f'--key={client}', '--bind=127.0.0.1', route['address'], f"{port}:{route['port']}"], env)
                entry = {'route': route, 'port': port, 'tunnel': tunnel, 'used': now,
                         'consecutive_failures': 0, 'last_error': '', 'last_success': None}
                self.forwarders[agent_id] = entry
            entry['used'] = now
            entry['tunnel'].tick(now)
            return entry

    def deliver(self, agent_id, payload):
        with self.lock:
            entry = self.endpoint(agent_id)
            entry['active'] = entry.get('active', 0) + 1
        route = entry['route']
        # Verify the explicitly pinned certificate BEFORE sending any HTTP data.
        connection = http.client.HTTPSConnection('127.0.0.1', entry['port'], timeout=5, context=ssl._create_unverified_context())
        try:
            connection.connect()
            actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if actual != route['tls_fingerprint']:
                raise ValueError('TLS pin mismatch')
            if payload is None:
                connection.request('GET', '/v1/direct/healthz')
            else:
                connection.request('POST', '/v1/direct/messages', json.dumps(payload), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('response too large')
            if response.status != 200 and response.status != 202:
                raise ValueError('peer rejected request')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError('invalid response')
            if payload is None:
                if result.get('agent_id') != agent_id or result.get('status') != 'ok':
                    raise ValueError('health response identity mismatch')
            elif result.get('message_id') != payload['envelope']['message_id'] or result.get('status') not in {'delivered', 'duplicate', 'accepted'}:
                raise ValueError('invalid acknowledgement')
            entry['tunnel'].failures = 0
            entry['consecutive_failures'] = 0
            entry['last_error'] = ''
            entry['last_success'] = int(time.time())
            return result
        except (OSError, http.client.HTTPException) as exc:
            with self.lock:
                # The forward listener may not exist on the first attempt. Do not
                # repeatedly kill a healthy child before Tailcat finishes starting.
                now = time.monotonic()
                entry['consecutive_failures'] = entry.get('consecutive_failures', 0) + 1
                entry['last_error'] = type(exc).__name__
                child = entry['tunnel'].child
                if child and child.poll() is None and now - entry['tunnel'].started >= 60 and entry['consecutive_failures'] >= 3:
                    entry['tunnel'].failed(now)
                    entry['consecutive_failures'] = 0
            raise
        finally:
            connection.close()
            with self.lock:
                entry['active'] -= 1

    def drain(self):
        now = time.time()
        for row in self.store.due(now):
            if self.stop_event.is_set():
                break
            peer = self.sidecar.trusted_peer(row['recipient_id'])
            if not peer or peer.signing_key != row['signing_key'] or not self.sidecar._private_trust_ready(row['recipient_id']):
                self.store.finish(row['message_id'], 'cancelled', 'trust_revoked')
                continue
            if row['expires_at'] <= now:
                self.store.finish(row['message_id'], 'expired', 'ttl_expired')
                continue
            try:
                self.deliver(row['recipient_id'], json.loads(row['body']))
                self.store.finish(row['message_id'], 'delivered')
            except ValueError:
                self.store.finish(row['message_id'], 'blocked', 'route_or_peer_rejected')
            except (OSError, RuntimeError, http.client.HTTPException):
                delay = random.uniform(.5, 1) * min(300, 2 ** min(row['attempts'] + 1, 9))
                self.store.finish(row['message_id'], 'queued', 'unreachable', now + delay)

    def probe(self, agent_id):
        started = time.monotonic()
        try:
            self.deliver(agent_id, None)
            return {'online': True, 'latency_ms': (time.monotonic() - started) * 1000, 'error': None}
        except (ValueError, OSError, RuntimeError, http.client.HTTPException) as exc:
            return {'online': False, 'latency_ms': None, 'error': type(exc).__name__}

    def start(self):
        def supervise():
            while not self.stop_event.is_set():
                try:
                    self.reconcile()
                except Exception:
                    self.errors += 1
                self.stop_event.wait(2)

        def work():
            while not self.stop_event.is_set():
                try:
                    self.drain()
                except Exception:
                    self.errors += 1
                self.stop_event.wait(2)

        def maintain():
            while not self.stop_event.wait(60):
                try:
                    self.sidecar.relay.register()
                    self.sidecar.refresh_capabilities()
                except Exception:
                    self.errors += 1
                try:
                    self.sidecar.sync_all_project_boards()
                except Exception:
                    self.errors += 1

        if self.thread:
            raise RuntimeError('network manager already started')
        self.thread = threading.Thread(target=work, name='sidecar-network', daemon=True)
        self.workers = [threading.Thread(target=supervise, name='tailcat-supervisor', daemon=True),
                        threading.Thread(target=maintain, name='network-maintenance', daemon=True)]
        self.thread.start()
        for worker in self.workers:
            worker.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        for worker in self.workers:
            worker.join()
        with self.lock:
            if self.server:
                self.server.stop()
            for entry in self.forwarders.values():
                entry['tunnel'].stop()
        self.store.close()
        self.lease.close()

    def status(self):
        with self.lock:
            return {'enabled': True, 'errors': self.errors, 'connections': len(self.forwarders),
                    'server_running': bool(self.server and self.server.child and self.server.child.poll() is None),
                    'server_restarts': self.server.failures if self.server else 0,
                    'server_last_exit': self.server.last_exit if self.server else None,
                    'peers': [{'agent_id': agent_id, 'last_success': entry.get('last_success'),
                               'last_error': entry.get('last_error', ''), 'process_restarts': entry['tunnel'].failures,
                               'process_running': bool(entry['tunnel'].child and entry['tunnel'].child.poll() is None),
                               'process_last_exit': entry['tunnel'].last_exit}
                              for agent_id, entry in self.forwarders.items()],
                    'outbox': self.store.status()}
