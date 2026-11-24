"""One local identity connected to ECS; no public package contains access secrets."""
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
import time
from urllib.parse import urlparse

from .desktop import DesktopRuntime, LoopbackHTTPServer, desktop_handler
from .online import OnlineSidecar
from .research import ResearchStore, make_research_handler

DEFAULT_RELAY = 'https://189.24.102.37:8787'


def load_access(path, allow_test_endpoint=False):
    path = Path(path)
    if not path.is_absolute() or path.stat().st_size > 32768:
        raise ValueError('invalid ECS access file')
    value = json.loads(path.read_text('utf-8'))
    if not isinstance(value, dict) or set(value) != {'schema', 'relay_url', 'relay_token', 'ca_pem', 'experiment_id'}:
        raise ValueError('invalid ECS access fields')
    if value['schema'] != 'sidecar-ecs-access/1':
        raise ValueError('invalid ECS access version')
    url = urlparse(value['relay_url'])
    if url.scheme != 'https' or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError('ECS requires a credential-free HTTPS origin')
    if not url.hostname or not url.port:
        raise ValueError('access file requires an explicit HTTPS host and port')
    for key, maximum in [('relay_token', 4096), ('ca_pem', 20000), ('experiment_id', 100)]:
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= maximum:
            raise ValueError('invalid access ' + key)
    if len(value['relay_token']) < 32 or not value['relay_token'].isascii() or any(c.isspace() for c in value['relay_token']):
        raise ValueError('invalid Relay access token')
    if 'PRIVATE KEY' in value['ca_pem']:
        raise ValueError('access file must not contain certificate private keys')
    ssl.create_default_context(cadata=value['ca_pem'])
    return value


class OnlineDesktopRuntime(DesktopRuntime):
    def __init__(self, data_dir, token, access_file, allow_test_endpoint=False):
        super().__init__(data_dir, token)
        self.access = load_access(access_file, allow_test_endpoint)
        self.stopped = threading.Event()
        self.worker = None
        self.research_store = None

    def start(self):
        try:
            ca = self.root / 'ecs-ca.pem'
            ca.write_text(self.access['ca_pem'], encoding='ascii')
            # Only the owned backend process uses this CA, never the system store.
            os.environ['SSL_CERT_FILE'] = str(ca)
            experiment = self.access['experiment_id']
            marker = self.root / 'experiment.json'
            if marker.exists() and json.loads(marker.read_text())['experiment_id'] != experiment:
                raise ValueError('cannot silently change an existing experiment')
            marker.write_text(json.dumps({'experiment_id': experiment}))
            admin = self.root / 'research-admin.private'
            if not admin.exists():
                admin.write_text(secrets.token_urlsafe(48))
                admin.chmod(0o600)
            self.research_store = ResearchStore(self.root / 'research.db', experiment)
            collector = LoopbackHTTPServer(('127.0.0.1', 0), make_research_handler(self.research_store, admin.read_text().strip()))
            self.servers.append(collector)
            self._start_server(collector)
            node = self.root / 'node'
            node.mkdir(mode=0o700, exist_ok=True)
            (node / 'toy-config.json').write_text(json.dumps({'mode': 'toy', 'experiment_id': experiment,
                'research_url': 'http://127.0.0.1:' + str(collector.server_port), 'summary': {'kind': 'demo'}}))
            self.sidecar = OnlineSidecar(node, self.access['relay_url'], self.access['relay_token'],
                                         hardware_profile='basic', allow_offline=True)
            self.stores.append(self.sidecar.store)
            server = LoopbackHTTPServer(('127.0.0.1', 0), None)
            self.url = 'http://127.0.0.1:' + str(server.server_port)
            base = desktop_handler(self.sidecar, self.token, self.url)
            sidecar = self.sidecar
            class Handler(base):
                def do_GET(self):
                    if self.path == '/v1/online/status':
                        if self.authorized():
                            self.send_json(200, sidecar.online_status())
                    else:
                        super().do_GET()
            server.RequestHandlerClass = Handler
            self.servers.append(server)
            self._start_server(server)
            self.sidecar.toy.start()
            self.worker = threading.Thread(target=self._work, daemon=True)
            self.worker.start()
            return self.url
        except Exception:
            self.close()
            raise

    def _work(self):
        announced = 0
        synced = 0
        while not self.stopped.is_set():
            try:
                now = time.monotonic()
                if now - announced >= 30:
                    self.sidecar.relay.register()
                    self.sidecar.announce_capabilities()
                    announced = now
                self.sidecar.pump_online()
                self.sidecar.retry_accepted_project_invitations()
                if now - synced >= 15 and self.sidecar.toy.research.status()['consent_active']:
                    self.sidecar.sync_all_project_boards()
                    synced = now
            except Exception as error:
                self.sidecar.online_error = type(error).__name__
            self.stopped.wait(3)

    def close(self):
        self.stopped.set()
        if self.sidecar:
            self.sidecar.online_stop.set()
        if self.worker:
            self.worker.join(timeout=15)
        super().close()
        if self.research_store:
            self.research_store.db.close()
            self.research_store = None
