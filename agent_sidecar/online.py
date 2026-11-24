"""Explicit ECS ciphertext transport; direct-only Sidecars remain unchanged."""
import hashlib
import json
import threading
import time
import uuid

from .crypto import canonical
from .protocol import decrypt_message, encrypt_message
from .service import Sidecar


class OnlineSidecar(Sidecar):
    def __init__(self, *args, **kwargs):
        self.online_lock = threading.RLock()
        self.online_error = None
        self.last_connected = None
        self.online_stop = threading.Event()
        super().__init__(*args, **kwargs)
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS online_outbox(
                    operation_id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, envelope TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', last_sent INTEGER NOT NULL DEFAULT 0,
                    scope TEXT NOT NULL, project_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS online_receipts(
                    message_id TEXT PRIMARY KEY, envelope TEXT NOT NULL);
            ''')

    def announce_capabilities(self):
        self.capabilities['online_transport'] = 'sidecar-ecs-ciphertext/1'
        self.capabilities['experiment_id'] = self.toy.config.get('experiment_id')
        profile = self.toy.profile().get('profile') or {}
        self.capabilities['nickname'] = profile.get('nickname', '')
        return super().announce_capabilities()

    def _direct_info_for(self, project_id, agent_id):
        return {'endpoint': 'https://ecs-ciphertext.invalid', 'enabled': True,
                'transport': 'relay-ciphertext', 'tls_fingerprint': ''}

    def peer_candidates(self, project_id=''):
        peers = super().peer_candidates(project_id)
        capabilities = {entry['sender_id']: entry for entry in self.capability_nodes()}
        now = time.time()
        for peer in peers:
            entry = capabilities.get(peer['agent_id'], {})
            data = entry.get('capabilities', {})
            compatible = data.get('online_transport') == 'sidecar-ecs-ciphertext/1'
            seen = entry.get('created_at') or 0
            peer.update(online=bool(compatible and 0 <= now - seen <= 90), last_seen_at=seen or None)
            peer['display_name'] = data.get('nickname') or peer['display_name']
            if compatible:
                peer['direct'] = self._direct_info_for(project_id, peer['agent_id'])
        return [peer for peer in peers if capabilities.get(peer['agent_id'], {}).get('capabilities', {}).get('experiment_id') == self.toy.config.get('experiment_id')]

    def _send_direct_to_peer(self, peer, endpoint, message_type, payload, ttl_seconds=86400,
                             project_id='', scope='project', trace_prefix='project.direct'):
        if scope not in {'private', 'project', 'membership', 'board'}:
            raise ValueError('unsupported encrypted Relay scope')
        operation = {'recipient': peer.agent_id, 'scope': scope, 'project_id': project_id,
                     'type': message_type, 'payload': payload}
        operation_id = (hashlib.sha256(canonical(operation)).hexdigest()
                        if scope in {'membership', 'board'} else str(uuid.uuid4()))
        with self.store.lock, self.store.db:
            row = self.store.db.execute('SELECT * FROM online_outbox WHERE operation_id=?', (operation_id,)).fetchone()
            if not row:
                inner = encrypt_message(self.identity, peer, self.store.next_sequence(peer.agent_id),
                                        message_type, payload, ttl_seconds, project_id)
                outer = encrypt_message(self.identity, peer, self.store.next_sequence(peer.agent_id),
                    'sidecar.online.delivery', {'operation_id': operation_id, 'scope': scope, 'envelope': inner}, ttl_seconds)
                self.store.db.execute('INSERT INTO online_outbox(operation_id,recipient_id,message_id,envelope,scope,project_id) VALUES (?,?,?,?,?,?)',
                    (operation_id, peer.agent_id, inner['message_id'], json.dumps(outer), scope, project_id))
                row = self.store.db.execute('SELECT * FROM online_outbox WHERE operation_id=?', (operation_id,)).fetchone()
        self.store.add_trace('online.delivery.queued', self.identity.agent_id, {'message_id': row['message_id'],
                            'recipient_id': peer.agent_id, 'project_id': project_id, 'transport': 'relay-ciphertext'})
        return {'message_id': row['message_id'], 'recipient_id': peer.agent_id,
                'status': row['status'], 'transport': 'relay-ciphertext'}

    def send_routed(self, target, message, ttl_seconds=86400):
        result = super().send_routed(target, message, ttl_seconds)
        if result.get('kind') != 'public':
            result['transport'] = 'relay-ciphertext'
        return result

    def _apply_delivery(self, outer, payload, sender):
        inner = payload.get('envelope') or {}
        if inner.get('sender_id') != sender.agent_id or inner.get('recipient_id') != self.identity.agent_id:
            raise ValueError('Relay envelope identities do not match')
        scope = payload.get('scope')
        body = decrypt_message(self.identity, inner, sender)
        project_id = inner.get('project_id', '')
        # Validate/apply idempotent control objects before recording the generic
        # envelope: a failed application must remain retryable after restart.
        if scope == 'membership':
            project = self.store.project(project_id)
            if not project or project['status'] != 'active' or project['owner_id'] != sender.agent_id:
                raise ValueError('membership owner not accepted')
            if body.get('type') != 'project.membership.manifest':
                raise ValueError('invalid membership payload')
            self.apply_project_manifest(body.get('payload') or {})
        elif scope == 'board':
            if not self.trusted_peer(sender.agent_id, project_id):
                raise ValueError('project sender not trusted')
            self.toy.project_guard(project_id, sender.agent_id)
            self.toy.peer_consent([sender.agent_id])
            if body.get('type') != 'project.board.events':
                raise ValueError('online board transport only accepts events')
            self.apply_project_board_events(project_id, (body.get('payload') or {}).get('events'))
        result = self.receive_direct({'scope': scope, 'envelope': inner}, transport='relay-ciphertext')
        if result['status'] not in {'accepted', 'delivered', 'duplicate'}:
            raise ValueError('delivery not accepted')
        receipt = encrypt_message(self.identity, sender, self.store.next_sequence(sender.agent_id),
            'sidecar.online.receipt', {'operation_id': payload['operation_id'], 'message_id': inner['message_id']})
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO online_receipts VALUES (?,?)', (outer['message_id'], json.dumps(receipt)))
        return receipt

    def pump_online(self):
        with self.online_lock:
            if self.online_stop.is_set():
                return
            self.online_error = None
            self.poll_peer_trust_notifications()
            self.poll_project_invitation_notifications()
            now = int(time.time())
            with self.store.lock:
                rows = self.store.db.execute("SELECT * FROM online_outbox WHERE status='queued' AND last_sent<? LIMIT 20", (now - 15,)).fetchall()
            for row in rows:
                if self.online_stop.is_set():
                    return
                envelope = json.loads(row['envelope'])
                invalid = ('expired' if envelope['expires_at'] <= now else
                           'cancelled' if row['scope'] == 'private' and not self._private_trust_ready(row['recipient_id']) else None)
                if row['scope'] in {'private', 'project', 'board'}:
                    try:
                        self.toy.peer_consent([row['recipient_id']])
                    except ValueError:
                        invalid = 'cancelled'
                if row['scope'] in {'project', 'board'}:
                    try:
                        self.toy.project_guard(row['project_id'], row['recipient_id'])
                        if not self.store.project_active(row['project_id']) or not self.trusted_peer(row['recipient_id'], row['project_id']):
                            invalid = 'cancelled'
                    except ValueError:
                        invalid = 'cancelled'
                if invalid:
                    with self.store.lock, self.store.db:
                        self.store.db.execute('UPDATE online_outbox SET status=? WHERE operation_id=?', (invalid, row['operation_id']))
                        self.store.db.execute("UPDATE private_messages SET status=? WHERE message_id=? AND direction='outgoing'", (invalid, row['message_id']))
                    continue
                try:
                    self.relay.send(envelope)
                except RuntimeError as error:
                    if 'HTTP 409' not in str(error):
                        raise
                with self.store.lock, self.store.db:
                    self.store.db.execute('UPDATE online_outbox SET last_sent=? WHERE operation_id=?', (now, row['operation_id']))
            for envelope in self.relay.inbox(200):
                if self.online_stop.is_set():
                    return
                sender = self.relay.identity_for(envelope['sender_id'])
                try:
                    body = decrypt_message(self.identity, envelope, sender)
                except ValueError:
                    continue
                payload = body.get('payload') or {}
                if body.get('type') == 'sidecar.online.receipt':
                    with self.store.lock, self.store.db:
                        row = self.store.db.execute('SELECT * FROM online_outbox WHERE operation_id=? AND recipient_id=? AND message_id=?',
                            (payload.get('operation_id'), sender.agent_id, payload.get('message_id'))).fetchone()
                        if row and row['status'] == 'queued':
                            self.store.db.execute("UPDATE online_outbox SET status='delivered' WHERE operation_id=?", (row['operation_id'],))
                            self.store.db.execute("UPDATE private_messages SET status='delivered' WHERE message_id=? AND direction='outgoing'", (row['message_id'],))
                    self.relay.acknowledge(envelope['message_id'])
                elif body.get('type') == 'sidecar.online.delivery':
                    try:
                        with self.store.lock:
                            saved = self.store.db.execute('SELECT envelope FROM online_receipts WHERE message_id=?', (envelope['message_id'],)).fetchone()
                        receipt = json.loads(saved[0]) if saved else self._apply_delivery(envelope, payload, sender)
                        try:
                            self.relay.send(receipt)
                        except RuntimeError as error:
                            if 'HTTP 409' not in str(error):
                                raise
                        self.relay.acknowledge(envelope['message_id'])
                    except (ValueError, RuntimeError):
                        self.online_error = '有通信等待权限确认或重试'
            self.last_connected = int(time.time())

    def receive(self, auto_ack=True):
        # Never let the generic inbox treat a scoped transport wrapper as chat.
        self.pump_online()
        return []

    def sync_project_board_merkle(self, project_id, max_rounds=4):
        return self.sync_project_board(project_id, force=True)

    def online_status(self):
        with self.store.lock:
            pending = self.store.db.execute("SELECT count(*) FROM online_outbox WHERE status='queued'").fetchone()[0]
            failed = self.store.db.execute("SELECT count(*) FROM online_outbox WHERE status IN ('cancelled','expired')").fetchone()[0]
        return {'mode': 'ecs', 'relay_url': self.relay.relay_url(), 'last_connected': self.last_connected,
                'pending_messages': pending, 'failed_messages': failed, 'error': self.online_error, 'transport': 'relay-ciphertext'}
