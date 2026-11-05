from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .crypto import PublicIdentity
from .key_store import ProjectKeyBackend, SQLiteProjectKeyBackend


class RelayStore:
    def __init__(self, path: Path):
        self.data_dir = path.parent
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    identity_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    envelope_json TEXT NOT NULL,
                    acknowledged_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS inbox_idx
                    ON messages(recipient_id, acknowledged_at, created_at);
                CREATE TABLE IF NOT EXISTS broadcasts (
                    broadcast_id TEXT PRIMARY KEY,
                    sender_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    broadcast_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS broadcasts_idx
                    ON broadcasts(created_at, broadcast_id);
                """
            )

    def close(self) -> None:
        db = getattr(self, "db", None)
        if db is not None:
            try:
                db.close()
            finally:
                self.db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def register(self, identity: PublicIdentity) -> None:
        identity.verify()
        encoded = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT identity_json FROM agents WHERE agent_id = ?", (identity.agent_id,)
            ).fetchone()
            if existing and existing["identity_json"] != encoded:
                raise ValueError("agent identity is already registered with different keys")
            self.db.execute(
                "INSERT OR IGNORE INTO agents(agent_id, identity_json, created_at) VALUES (?, ?, ?)",
                (identity.agent_id, encoded, int(time.time())),
            )

    def identities(self) -> List[PublicIdentity]:
        with self.lock:
            rows = self.db.execute("SELECT identity_json FROM agents ORDER BY created_at, agent_id").fetchall()
        return [PublicIdentity.from_dict(json.loads(row["identity_json"])) for row in rows]

    def identity(self, agent_id: str) -> Optional[PublicIdentity]:
        with self.lock:
            row = self.db.execute("SELECT identity_json FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return PublicIdentity.from_dict(json.loads(row["identity_json"])) if row else None

    def enqueue(self, envelope: Dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO messages
                   (message_id, sender_id, recipient_id, created_at, expires_at, envelope_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    envelope["message_id"],
                    envelope["sender_id"],
                    envelope["recipient_id"],
                    envelope["created_at"],
                    envelope["expires_at"],
                    json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                ),
            )

    def inbox(self, recipient_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        now = int(time.time())
        with self.lock:
            rows = self.db.execute(
                """SELECT envelope_json FROM messages
                   WHERE recipient_id = ? AND acknowledged_at IS NULL AND expires_at >= ?
                   ORDER BY created_at, message_id LIMIT ?""",
                (recipient_id, now, min(max(limit, 1), 500)),
            ).fetchall()
        return [json.loads(row["envelope_json"]) for row in rows]

    def acknowledge(self, recipient_id: str, message_id: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute(
                """UPDATE messages SET acknowledged_at = ?
                   WHERE message_id = ? AND recipient_id = ? AND acknowledged_at IS NULL""",
                (int(time.time()), message_id, recipient_id),
            )
        return cursor.rowcount == 1


    def enqueue_broadcast(self, broadcast: Dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO broadcasts(broadcast_id, sender_id, channel, created_at, expires_at, broadcast_json) VALUES (?, ?, ?, ?, ?, ?)", (broadcast["broadcast_id"], broadcast["sender_id"], broadcast["channel"], broadcast["created_at"], broadcast["expires_at"], json.dumps(broadcast, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))

    def broadcasts(self, limit: int = 100, channel: Optional[str] = None, since: int = 0) -> List[Dict[str, Any]]:
        now = int(time.time())
        with self.lock:
            if channel:
                rows = self.db.execute("SELECT broadcast_json FROM broadcasts WHERE channel = ? AND created_at >= ? AND expires_at >= ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (channel, int(since), now, min(max(int(limit), 1), 500))).fetchall()
            else:
                rows = self.db.execute("SELECT broadcast_json FROM broadcasts WHERE created_at >= ? AND expires_at >= ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (int(since), now, min(max(int(limit), 1), 500))).fetchall()
        return [json.loads(row["broadcast_json"]) for row in rows]


class LocalStore:
    def __init__(self, path: Path, project_key_backend: Optional[ProjectKeyBackend] = None):
        self.data_dir = path.parent
        self.project_key_backend = project_key_backend or SQLiteProjectKeyBackend()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS peers (
                    agent_id TEXT PRIMARY KEY,
                    identity_json TEXT NOT NULL,
                    trusted_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS counters (
                    recipient_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS peer_trust_statements (
                    statement_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    issuer_agent_id TEXT NOT NULL,
                    subject_agent_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    statement_json TEXT NOT NULL,
                    envelope_message_id TEXT UNIQUE,
                    recorded_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS peer_trust_statements_idx
                    ON peer_trust_statements(direction, issuer_agent_id, subject_agent_id, issued_at);
                CREATE TABLE IF NOT EXISTS project_invitations (
                    invitation_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    owner_agent_id TEXT NOT NULL,
                    invitee_agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    invitation_json TEXT NOT NULL,
                    response_json TEXT,
                    invitation_message_id TEXT,
                    response_message_id TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS project_invitations_idx
                    ON project_invitations(direction, status, project_id, updated_at);
                CREATE TABLE IF NOT EXISTS received (
                    message_id TEXT PRIMARY KEY,
                    sender_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    body_json TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS peer_direct_contacts (
                    agent_id TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    tls_fingerprint TEXT NOT NULL DEFAULT '',
                    protocol TEXT NOT NULL DEFAULT 'sidecar-direct/1',
                    source TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS private_messages (
                    message_id TEXT PRIMARY KEY,
                    peer_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS private_messages_peer_idx
                    ON private_messages(peer_id, created_at, message_id);
                CREATE TABLE IF NOT EXISTS fl_rounds (
                    round_id TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL,
                    model_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS fl_updates (
                    update_id TEXT PRIMARY KEY,
                    round_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    model_hash TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fl_recoveries (
                    recovery_id TEXT PRIMARY KEY,
                    round_id TEXT NOT NULL,
                    survivor_id TEXT NOT NULL,
                    missing_id TEXT NOT NULL,
                    mask_hash TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fl_coordinator_state (
                    state_id TEXT PRIMARY KEY,
                    model_json TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    model_hash TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fl_coordinator_lease (
                    lease_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broadcast_receipts (
                    broadcast_id TEXT PRIMARY KEY,
                    received_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bootstrap_state (
                    state_id TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    installed_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    closed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS project_peers (
                    project_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    trusted_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    PRIMARY KEY(project_id, agent_id)
                );
                CREATE INDEX IF NOT EXISTS project_peers_idx
                    ON project_peers(project_id, revoked_at, agent_id);
                CREATE TABLE IF NOT EXISTS project_members (
                    project_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    direct_endpoint TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    PRIMARY KEY(project_id, agent_id)
                );
                CREATE INDEX IF NOT EXISTS project_members_idx
                    ON project_members(project_id, revoked_at, agent_id);
                CREATE TABLE IF NOT EXISTS project_settings (
                    project_id TEXT PRIMARY KEY,
                    auto_trust_members INTEGER NOT NULL DEFAULT 0,
                    gossip_fanout INTEGER NOT NULL DEFAULT 2,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_manifests (
                    project_id TEXT PRIMARY KEY,
                    epoch INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_manifest_history (
                    project_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    PRIMARY KEY(project_id, epoch)
                );
                CREATE INDEX IF NOT EXISTS project_manifest_history_idx
                    ON project_manifest_history(project_id, epoch);
                CREATE TABLE IF NOT EXISTS project_content_keys (
                    project_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    key_b64 TEXT NOT NULL,
                    key_fingerprint TEXT NOT NULL,
                    installed_at INTEGER NOT NULL,
                    PRIMARY KEY(project_id, epoch)
                );
                CREATE TABLE IF NOT EXISTS project_board_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    author_agent_id TEXT NOT NULL,
                    membership_epoch INTEGER NOT NULL,
                    logical_clock INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    parent_event_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS project_board_events_idx
                    ON project_board_events(project_id, logical_clock, created_at, event_id);
                CREATE TABLE IF NOT EXISTS project_board_sync_state (
                    project_id TEXT NOT NULL,
                    peer_agent_id TEXT NOT NULL,
                    event_ids_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(project_id, peer_agent_id)
                );
                CREATE TABLE IF NOT EXISTS project_gossip_peer_metrics (
                    project_id TEXT NOT NULL,
                    peer_agent_id TEXT NOT NULL,
                    success_score REAL NOT NULL DEFAULT 0,
                    failure_score REAL NOT NULL DEFAULT 0,
                    last_success_at INTEGER,
                    last_failure_at INTEGER,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(project_id, peer_agent_id)
                );
                CREATE TABLE IF NOT EXISTS project_gossip_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    peer_agent_id TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    recorded_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS project_gossip_attempts_idx
                    ON project_gossip_attempts(project_id, peer_agent_id, recorded_at DESC);
                """
            )
            try:
                self.db.execute("ALTER TABLE fl_rounds ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'" )
            except sqlite3.OperationalError:
                pass
            for column, definition in (("trust_state", "TEXT NOT NULL DEFAULT 'trusted'"), ("revoked_at", "INTEGER"), ("last_seen_at", "INTEGER"), ("display_name", "TEXT NOT NULL DEFAULT ''"), ("capabilities_json", "TEXT NOT NULL DEFAULT '{}'"), ("key_fingerprint", "TEXT NOT NULL DEFAULT ''")):
                try:
                    self.db.execute(f"ALTER TABLE peers ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:
                    pass
            try:
                self.db.execute("ALTER TABLE project_settings ADD COLUMN gossip_fanout INTEGER NOT NULL DEFAULT 2")
            except sqlite3.OperationalError:
                pass
            self.db.execute(
                "INSERT OR IGNORE INTO project_manifest_history(project_id, epoch, manifest_json, signature, recorded_at) SELECT project_id, epoch, manifest_json, signature, updated_at FROM project_manifests"
            )
        self._migrate_project_keys_to_backend()

    def _migrate_project_keys_to_backend(self) -> None:
        if self.project_key_backend.name == "sqlite":
            return
        with self.lock:
            rows = self.db.execute(
                "SELECT project_id, epoch, key_b64, key_fingerprint FROM project_content_keys WHERE key_b64 != ''"
            ).fetchall()
        for row in rows:
            self.project_key_backend.save(row["project_id"], row["epoch"], row["key_b64"], row["key_fingerprint"])
        if rows:
            with self.lock, self.db:
                self.db.execute("UPDATE project_content_keys SET key_b64 = '' WHERE key_b64 != ''")

    def close(self) -> None:
        db = getattr(self, "db", None)
        if db is not None:
            try:
                db.close()
            finally:
                self.db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def trust(self, identity: PublicIdentity) -> None:
        identity.verify()
        encoded = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT identity_json FROM peers WHERE agent_id = ?", (identity.agent_id,)
            ).fetchone()
            if existing and existing["identity_json"] != encoded:
                raise ValueError("peer key changed; explicit key rotation is required")
            now = int(time.time())
            self.db.execute(
                "INSERT INTO peers(agent_id, identity_json, trusted_at, trust_state, revoked_at, last_seen_at, key_fingerprint) VALUES (?, ?, ?, 'trusted', NULL, ?, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET identity_json=excluded.identity_json, trust_state='trusted', revoked_at=NULL, last_seen_at=excluded.last_seen_at, key_fingerprint=excluded.key_fingerprint",
                (identity.agent_id, encoded, now, now, identity.signing_key[:16]),
            )

    def revoke(self, agent_id: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE peers SET trust_state = 'revoked', revoked_at = ? WHERE agent_id = ?", (int(time.time()), agent_id))
        return cursor.rowcount == 1

    def block(self, identity: PublicIdentity) -> None:
        identity.verify()
        encoded = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT identity_json FROM peers WHERE agent_id = ?", (identity.agent_id,)
            ).fetchone()
            if existing and existing["identity_json"] != encoded:
                raise ValueError("peer key changed; explicit key rotation is required")
            now = int(time.time())
            self.db.execute(
                "INSERT INTO peers(agent_id, identity_json, trusted_at, trust_state, revoked_at, last_seen_at, key_fingerprint) VALUES (?, ?, ?, 'blocked', ?, ?, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET identity_json=excluded.identity_json, trust_state='blocked', revoked_at=excluded.revoked_at, last_seen_at=excluded.last_seen_at, key_fingerprint=excluded.key_fingerprint",
                (identity.agent_id, encoded, now, now, now, identity.signing_key[:16]),
            )

    def save_peer_trust_statement(self, statement: Dict[str, Any], direction: str, envelope_message_id: str = "") -> bool:
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("node trust statement direction must be incoming or outgoing")
        encoded = json.dumps(statement, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT direction, statement_json FROM peer_trust_statements WHERE statement_id = ?",
                (statement["statement_id"],),
            ).fetchone()
            if existing:
                if existing["direction"] != direction or existing["statement_json"] != encoded:
                    raise ValueError("node trust statement ID was reused with different content")
                return False
            self.db.execute(
                "INSERT INTO peer_trust_statements(statement_id, direction, issuer_agent_id, subject_agent_id, decision, issued_at, statement_json, envelope_message_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    statement["statement_id"],
                    direction,
                    statement["issuer_agent_id"],
                    statement["subject_agent_id"],
                    statement["decision"],
                    int(statement["issued_at"]),
                    encoded,
                    envelope_message_id or None,
                    int(time.time()),
                ),
            )
        return True

    def peer_trust_statements(self, direction: str = "", peer_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        clauses = []
        values: List[Any] = []
        if direction:
            clauses.append("direction = ?")
            values.append(direction)
        if peer_id:
            clauses.append("(issuer_agent_id = ? OR subject_agent_id = ?)")
            values.extend((peer_id, peer_id))
        query = "SELECT direction, envelope_message_id, recorded_at, statement_json FROM peer_trust_statements"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY issued_at DESC, rowid DESC LIMIT ?"
        values.append(min(max(int(limit), 1), 1000))
        with self.lock:
            rows = self.db.execute(query, tuple(values)).fetchall()
        return [
            {
                **json.loads(row["statement_json"]),
                "direction": row["direction"],
                "envelope_message_id": row["envelope_message_id"],
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        ]

    def latest_peer_trust_statement(self, peer_id: str, direction: str) -> Optional[Dict[str, Any]]:
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("node trust statement direction must be incoming or outgoing")
        column = "issuer_agent_id" if direction == "incoming" else "subject_agent_id"
        with self.lock:
            row = self.db.execute(
                f"SELECT statement_json FROM peer_trust_statements WHERE direction = ? AND {column} = ? ORDER BY issued_at DESC, rowid DESC LIMIT 1",
                (direction, peer_id),
            ).fetchone()
        return json.loads(row["statement_json"]) if row else None

    def save_project_invitation(self, invitation: Dict[str, Any], direction: str, message_id: str = "") -> bool:
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("project invitation direction must be incoming or outgoing")
        encoded = json.dumps(invitation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT direction, invitation_json FROM project_invitations WHERE invitation_id = ?",
                (invitation["invitation_id"],),
            ).fetchone()
            if existing:
                if existing["direction"] != direction or existing["invitation_json"] != encoded:
                    raise ValueError("project invitation ID was reused with different content")
                return False
            now = int(time.time())
            self.db.execute(
                "INSERT INTO project_invitations(invitation_id, direction, project_id, owner_agent_id, invitee_agent_id, status, invitation_json, invitation_message_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    invitation["invitation_id"],
                    direction,
                    invitation["project_id"],
                    invitation["owner_agent_id"],
                    invitation["invitee_agent_id"],
                    encoded,
                    message_id or None,
                    now,
                    now,
                ),
            )
        return True

    def project_invitation(self, invitation_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute(
                "SELECT direction, status, invitation_json, response_json, invitation_message_id, response_message_id, created_at, updated_at FROM project_invitations WHERE invitation_id = ?",
                (invitation_id,),
            ).fetchone()
        if not row:
            return None
        return {
            **json.loads(row["invitation_json"]),
            "direction": row["direction"],
            "status": row["status"],
            "response": json.loads(row["response_json"]) if row["response_json"] else None,
            "invitation_message_id": row["invitation_message_id"],
            "response_message_id": row["response_message_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_project_invitation(self, invitation_id: str, status: str, response: Optional[Dict[str, Any]] = None, response_message_id: str = "") -> Dict[str, Any]:
        if status not in {"pending", "accepted", "rejected", "completed", "failed"}:
            raise ValueError("unsupported project invitation status")
        response_json = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if response else None
        with self.lock, self.db:
            cursor = self.db.execute(
                "UPDATE project_invitations SET status = ?, response_json = COALESCE(?, response_json), response_message_id = COALESCE(?, response_message_id), updated_at = ? WHERE invitation_id = ?",
                (status, response_json, response_message_id or None, int(time.time()), invitation_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("project invitation not found")
        return self.project_invitation(invitation_id) or {}

    def project_invitations(self, direction: str = "", status: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        clauses = []
        values: List[Any] = []
        if direction:
            clauses.append("direction = ?")
            values.append(direction)
        if status:
            clauses.append("status = ?")
            values.append(status)
        query = "SELECT invitation_id FROM project_invitations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, rowid DESC LIMIT ?"
        values.append(min(max(int(limit), 1), 1000))
        with self.lock:
            rows = self.db.execute(query, tuple(values)).fetchall()
        return [value for row in rows if (value := self.project_invitation(row["invitation_id"])) is not None]

    def set_trusted(self, identities: List[PublicIdentity]) -> None:
        desired = {identity.agent_id: identity for identity in identities}
        with self.lock, self.db:
            rows = self.db.execute("SELECT agent_id FROM peers").fetchall()
            for row in rows:
                if row["agent_id"] not in desired:
                    self.db.execute("UPDATE peers SET trust_state = 'revoked', revoked_at = ? WHERE agent_id = ?", (int(time.time()), row["agent_id"]))
        for identity in identities:
            self.trust(identity)

    def peer(self, agent_id: str) -> Optional[PublicIdentity]:
        with self.lock:
            row = self.db.execute("SELECT identity_json FROM peers WHERE agent_id = ?", (agent_id,)).fetchone()
        return PublicIdentity.from_dict(json.loads(row["identity_json"])) if row else None

    def peers(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT identity_json, trusted_at, trust_state, revoked_at, last_seen_at, display_name, capabilities_json, key_fingerprint FROM peers ORDER BY trusted_at").fetchall()
        output = []
        for row in rows:
            value = {**json.loads(row["identity_json"]), "trusted_at": row["trusted_at"], "trust_state": row["trust_state"], "revoked_at": row["revoked_at"], "last_seen_at": row["last_seen_at"], "display_name": row["display_name"], "capabilities": json.loads(row["capabilities_json"] or "{}"), "key_fingerprint": row["key_fingerprint"]}
            output.append(value)
        return output

    def trusted_ids(self) -> List[str]:
        with self.lock:
            rows = self.db.execute("SELECT agent_id FROM peers WHERE trust_state = 'trusted'").fetchall()
        return [row["agent_id"] for row in rows]

    def save_peer_direct_contact(self, agent_id: str, endpoint: str, tls_fingerprint: str = "", protocol: str = "sidecar-direct/1", source: str = "signed") -> None:
        normalized_endpoint = str(endpoint or "").rstrip("/")
        if not normalized_endpoint:
            return
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO peer_direct_contacts(agent_id, endpoint, tls_fingerprint, protocol, source, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET endpoint=excluded.endpoint, tls_fingerprint=excluded.tls_fingerprint, protocol=excluded.protocol, source=excluded.source, updated_at=excluded.updated_at",
                (agent_id, normalized_endpoint, str(tls_fingerprint or "").lower().replace(":", ""), str(protocol or "sidecar-direct/1"), str(source or "signed"), int(time.time())),
            )

    def peer_direct_contact(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute(
                "SELECT agent_id, endpoint, tls_fingerprint, protocol, source, updated_at FROM peer_direct_contacts WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_private_message(self, message_id: str, peer_id: str, direction: str, message_type: str, payload: Any, created_at: int, status: str = "delivered") -> bool:
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("private message direction must be incoming or outgoing")
        try:
            with self.lock, self.db:
                self.db.execute(
                    "INSERT INTO private_messages(message_id, peer_id, direction, message_type, payload_json, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (message_id, peer_id, direction, str(message_type or "message"), json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), int(created_at), str(status or "delivered")),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def private_messages(self, peer_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        query = "SELECT message_id, peer_id, direction, message_type, payload_json, created_at, status FROM private_messages"
        values: list[Any] = []
        if peer_id:
            query += " WHERE peer_id = ?"
            values.append(peer_id)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        values.append(min(max(int(limit), 1), 1000))
        with self.lock:
            rows = self.db.execute(query, tuple(values)).fetchall()
        return [
            {
                "message_id": row["message_id"],
                "peer_id": row["peer_id"],
                "direction": row["direction"],
                "type": row["message_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
                "status": row["status"],
            }
            for row in reversed(rows)
        ]

    def create_project(self, project_id: str, name: str, owner_id: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", project_id):
            raise ValueError("project_id must contain only letters, numbers, '.', '_' or '-'")
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("project name is required")
        now = int(time.time())
        with self.lock, self.db:
            # Project creation is intentionally idempotent for retries from the
            # dashboard/API. A successful request followed by a timeout should
            # not surface SQLite's raw UNIQUE constraint on the retry.
            self.db.execute(
                "INSERT OR IGNORE INTO projects(project_id, name, owner_id, status, created_at) VALUES (?, ?, ?, 'active', ?)",
                (project_id, normalized_name, owner_id, now),
            )
            row = self.db.execute(
                "SELECT project_id, name, owner_id, status, created_at, closed_at FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise ValueError("project could not be created")
            if row["name"] != normalized_name or row["owner_id"] != owner_id:
                raise ValueError("project_id already exists with a different name or owner")
            if row["status"] != "active":
                raise ValueError("project_id already exists and is closed")
        return self.project(project_id) or {}

    def project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute("SELECT project_id, name, owner_id, status, created_at, closed_at FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            members = self.db.execute("SELECT agent_id, trusted_at, revoked_at FROM project_peers WHERE project_id = ? ORDER BY trusted_at, agent_id", (project_id,)).fetchall() if row else []
        if not row:
            return None
        value = dict(row)
        value["members"] = [dict(member) for member in members]
        return value

    def projects(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT project_id FROM projects ORDER BY created_at DESC, project_id").fetchall()
        return [value for row in rows if (value := self.project(row["project_id"])) is not None]

    def project_active(self, project_id: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT 1 FROM projects WHERE project_id = ? AND status = 'active'", (project_id,)).fetchone()
        return row is not None

    def trust_for_project(self, project_id: str, identity: PublicIdentity) -> None:
        identity.verify()
        encoded = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            project = self.db.execute("SELECT status FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            if not project:
                raise ValueError("project not found")
            if project["status"] != "active":
                raise ValueError("project is closed")
            existing = self.db.execute("SELECT identity_json FROM project_peers WHERE project_id = ? AND agent_id = ?", (project_id, identity.agent_id)).fetchone()
            if existing and existing["identity_json"] != encoded:
                raise ValueError("project peer key changed; explicit key rotation is required")
            self.db.execute("INSERT INTO project_peers(project_id, agent_id, identity_json, trusted_at, revoked_at) VALUES (?, ?, ?, ?, NULL) ON CONFLICT(project_id, agent_id) DO UPDATE SET identity_json=excluded.identity_json, trusted_at=excluded.trusted_at, revoked_at=NULL", (project_id, identity.agent_id, encoded, int(time.time())))

    def upsert_project_member(self, project_id: str, identity: PublicIdentity, direct_endpoint: str = "", role: str = "member", revoked_at: Optional[int] = None) -> None:
        identity.verify()
        encoded = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            project = self.db.execute("SELECT status FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            if not project:
                raise ValueError("project not found")
            if project["status"] != "active" and revoked_at is None:
                raise ValueError("project is closed")
            existing = self.db.execute("SELECT identity_json FROM project_members WHERE project_id = ? AND agent_id = ?", (project_id, identity.agent_id)).fetchone()
            if existing and existing["identity_json"] != encoded:
                raise ValueError("project member key changed; explicit key rotation is required")
            self.db.execute(
                "INSERT INTO project_members(project_id, agent_id, identity_json, direct_endpoint, role, joined_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, agent_id) DO UPDATE SET identity_json=excluded.identity_json, direct_endpoint=excluded.direct_endpoint, role=excluded.role, revoked_at=excluded.revoked_at",
                (project_id, identity.agent_id, encoded, str(direct_endpoint or ""), str(role or "member"), int(time.time()), revoked_at),
            )

    def project_members(self, project_id: str, include_revoked: bool = False) -> List[Dict[str, Any]]:
        with self.lock:
            query = "SELECT agent_id, identity_json, direct_endpoint, role, joined_at, revoked_at FROM project_members WHERE project_id = ?"
            args: list[Any] = [project_id]
            if not include_revoked:
                query += " AND revoked_at IS NULL"
            query += " ORDER BY joined_at, agent_id"
            rows = self.db.execute(query, args).fetchall()
        output = []
        for row in rows:
            identity = json.loads(row["identity_json"])
            output.append({**identity, "direct_endpoint": row["direct_endpoint"], "role": row["role"], "joined_at": row["joined_at"], "revoked_at": row["revoked_at"]})
        return output

    def project_member(self, project_id: str, agent_id: str) -> Optional[PublicIdentity]:
        with self.lock:
            row = self.db.execute("SELECT identity_json FROM project_members WHERE project_id = ? AND agent_id = ? AND revoked_at IS NULL", (project_id, agent_id)).fetchone()
        return PublicIdentity.from_dict(json.loads(row["identity_json"])) if row else None

    def revoke_project_member(self, project_id: str, agent_id: str) -> bool:
        now = int(time.time())
        with self.lock, self.db:
            member = self.db.execute("UPDATE project_members SET revoked_at = COALESCE(revoked_at, ?) WHERE project_id = ? AND agent_id = ?", (now, project_id, agent_id))
            self.db.execute("UPDATE project_peers SET revoked_at = COALESCE(revoked_at, ?) WHERE project_id = ? AND agent_id = ?", (now, project_id, agent_id))
        return member.rowcount == 1

    def project_member_endpoint(self, project_id: str, agent_id: str) -> str:
        with self.lock:
            row = self.db.execute("SELECT direct_endpoint FROM project_members WHERE project_id = ? AND agent_id = ? AND revoked_at IS NULL", (project_id, agent_id)).fetchone()
        return str(row["direct_endpoint"] or "") if row else ""

    def set_project_member_endpoint(self, project_id: str, agent_id: str, endpoint: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute(
                "UPDATE project_members SET direct_endpoint = ? WHERE project_id = ? AND agent_id = ? AND revoked_at IS NULL",
                (str(endpoint or "").rstrip("/"), project_id, agent_id),
            )
        return cursor.rowcount == 1

    def set_project_auto_trust(self, project_id: str, enabled: bool) -> None:
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone():
                raise ValueError("project not found")
            self.db.execute("INSERT INTO project_settings(project_id, auto_trust_members, updated_at) VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET auto_trust_members=excluded.auto_trust_members, updated_at=excluded.updated_at", (project_id, 1 if enabled else 0, int(time.time())))

    def set_project_gossip_fanout(self, project_id: str, fanout: int) -> None:
        fanout = int(fanout)
        if fanout < 1 or fanout > 32:
            raise ValueError("project gossip fanout must be between 1 and 32")
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone():
                raise ValueError("project not found")
            self.db.execute(
                "INSERT INTO project_settings(project_id, auto_trust_members, gossip_fanout, updated_at) VALUES (?, COALESCE((SELECT auto_trust_members FROM project_settings WHERE project_id = ?), 0), ?, ?) ON CONFLICT(project_id) DO UPDATE SET gossip_fanout=excluded.gossip_fanout, updated_at=excluded.updated_at",
                (project_id, project_id, fanout, int(time.time())),
            )

    def project_gossip_fanout(self, project_id: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT gossip_fanout FROM project_settings WHERE project_id = ?", (project_id,)).fetchone()
        return max(1, min(int(row["gossip_fanout"]), 32)) if row else 2

    def project_auto_trust(self, project_id: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT auto_trust_members FROM project_settings WHERE project_id = ?", (project_id,)).fetchone()
        return bool(row and row["auto_trust_members"])

    def save_project_manifest(self, project_id: str, epoch: int, manifest: Dict[str, Any], signature: str) -> bool:
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        epoch = int(epoch)
        with self.lock, self.db:
            historical = self.db.execute(
                "SELECT manifest_json, signature FROM project_manifest_history WHERE project_id = ? AND epoch = ?",
                (project_id, epoch),
            ).fetchone()
            if historical:
                if historical["manifest_json"] != encoded or historical["signature"] != signature:
                    raise ValueError("project membership epoch was reused with different content")
            else:
                self.db.execute(
                    "INSERT INTO project_manifest_history(project_id, epoch, manifest_json, signature, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (project_id, epoch, encoded, signature, int(time.time())),
                )
            row = self.db.execute("SELECT epoch FROM project_manifests WHERE project_id = ?", (project_id,)).fetchone()
            if row and epoch <= int(row["epoch"]):
                return False
            self.db.execute("INSERT INTO project_manifests(project_id, epoch, manifest_json, signature, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET epoch=excluded.epoch, manifest_json=excluded.manifest_json, signature=excluded.signature, updated_at=excluded.updated_at", (project_id, epoch, encoded, signature, int(time.time())))
        return True

    def project_manifest(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute("SELECT epoch, manifest_json, signature, updated_at FROM project_manifests WHERE project_id = ?", (project_id,)).fetchone()
        if not row:
            return None
        return {"epoch": row["epoch"], "manifest": json.loads(row["manifest_json"]), "signature": row["signature"], "updated_at": row["updated_at"]}

    def project_manifest_at(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute(
                "SELECT epoch, manifest_json, signature, recorded_at FROM project_manifest_history WHERE project_id = ? AND epoch = ?",
                (project_id, int(epoch)),
            ).fetchone()
        if not row:
            return None
        return {"epoch": row["epoch"], "manifest": json.loads(row["manifest_json"]), "signature": row["signature"], "recorded_at": row["recorded_at"]}

    def project_manifest_history(self, project_id: str, limit: int = 1000) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT epoch, manifest_json, signature, recorded_at FROM project_manifest_history WHERE project_id = ? ORDER BY epoch LIMIT ?",
                (project_id, min(max(int(limit), 1), 5000)),
            ).fetchall()
        return [
            {"epoch": row["epoch"], "manifest": json.loads(row["manifest_json"]), "signature": row["signature"], "recorded_at": row["recorded_at"]}
            for row in rows
        ]

    def save_project_content_key(self, project_id: str, epoch: int, key_b64: str, key_fingerprint: str) -> None:
        stored_key = key_b64 if self.project_key_backend.name == "sqlite" else ""
        if self.project_key_backend.name != "sqlite":
            self.project_key_backend.save(project_id, epoch, key_b64, key_fingerprint)
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT key_b64, key_fingerprint FROM project_content_keys WHERE project_id = ? AND epoch = ?",
                (project_id, int(epoch)),
            ).fetchone()
            if existing and existing["key_fingerprint"] != key_fingerprint:
                raise ValueError("project content key was reused with different material")
            self.db.execute(
                "INSERT INTO project_content_keys(project_id, epoch, key_b64, key_fingerprint, installed_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, epoch) DO NOTHING",
                (project_id, int(epoch), stored_key, key_fingerprint, int(time.time())),
            )

    def project_content_key(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute(
                "SELECT epoch, key_b64, key_fingerprint, installed_at FROM project_content_keys WHERE project_id = ? AND epoch = ?",
                (project_id, int(epoch)),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        if not value.get("key_b64") and self.project_key_backend.name != "sqlite":
            external = self.project_key_backend.get(project_id, epoch)
            if external:
                if external.get("key_fingerprint") != value.get("key_fingerprint"):
                    raise ValueError("external project key fingerprint does not match local metadata")
                value["key_b64"] = external.get("key_b64", "")
        return value

    def project_content_keys(self, project_id: str) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT epoch, key_b64, key_fingerprint, installed_at FROM project_content_keys WHERE project_id = ? ORDER BY epoch",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def next_project_board_clock(self, project_id: str) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT COALESCE(MAX(logical_clock), 0) AS logical_clock FROM project_board_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["logical_clock"] or 0) + 1

    def save_project_board_event(self, event: Dict[str, Any]) -> bool:
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute(
                "SELECT event_json FROM project_board_events WHERE event_id = ?",
                (event["event_id"],),
            ).fetchone()
            if existing:
                if existing["event_json"] != encoded:
                    raise ValueError("project board event ID was reused with different content")
                return False
            self.db.execute(
                "INSERT INTO project_board_events(event_id, project_id, author_agent_id, membership_epoch, logical_clock, event_type, parent_event_id, created_at, event_json, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["event_id"],
                    event["project_id"],
                    event["author_agent_id"],
                    int(event["membership_epoch"]),
                    int(event["logical_clock"]),
                    event["event_type"],
                    str(event.get("parent_event_id") or ""),
                    int(event["created_at"]),
                    encoded,
                    int(time.time()),
                ),
            )
        return True

    def project_board_events(self, project_id: str, after_clock: int = 0, limit: int = 1000) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT event_json FROM project_board_events WHERE project_id = ? AND logical_clock >= ? ORDER BY logical_clock, created_at, event_id LIMIT ?",
                (project_id, max(int(after_clock), 0), min(max(int(limit), 1), 5000)),
            ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

    def project_board_event_count(self, project_id: str) -> int:
        with self.lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS event_count FROM project_board_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["event_count"] or 0)

    def project_board_sent_event_ids(self, project_id: str, peer_agent_id: str) -> set[str]:
        with self.lock:
            row = self.db.execute(
                "SELECT event_ids_json FROM project_board_sync_state WHERE project_id = ? AND peer_agent_id = ?",
                (project_id, peer_agent_id),
            ).fetchone()
        if not row:
            return set()
        try:
            value = json.loads(row["event_ids_json"])
        except (TypeError, json.JSONDecodeError):
            return set()
        return {str(item) for item in value} if isinstance(value, list) else set()

    def record_project_gossip_result(self, project_id: str, peer_agent_id: str, success: bool, now: Optional[int] = None) -> None:
        import uuid
        timestamp = int(now or time.time())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO project_gossip_attempts(attempt_id, project_id, peer_agent_id, success, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), project_id, peer_agent_id, 1 if success else 0, timestamp),
            )
            self.db.execute(
                "DELETE FROM project_gossip_attempts WHERE project_id = ? AND peer_agent_id = ? AND attempt_id NOT IN (SELECT attempt_id FROM project_gossip_attempts WHERE project_id = ? AND peer_agent_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT 100)",
                (project_id, peer_agent_id, project_id, peer_agent_id),
            )
            row = self.db.execute(
                "SELECT success_score, failure_score, last_success_at, last_failure_at FROM project_gossip_peer_metrics WHERE project_id = ? AND peer_agent_id = ?",
                (project_id, peer_agent_id),
            ).fetchone()
            success_score = float(row["success_score"] if row else 0.0)
            failure_score = float(row["failure_score"] if row else 0.0)
            if success:
                success_score += 1.0
                last_success_at = timestamp
                last_failure_at = row["last_failure_at"] if row else None
            else:
                failure_score += 1.0
                last_success_at = row["last_success_at"] if row else None
                last_failure_at = timestamp
            self.db.execute(
                "INSERT INTO project_gossip_peer_metrics(project_id, peer_agent_id, success_score, failure_score, last_success_at, last_failure_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, peer_agent_id) DO UPDATE SET success_score=excluded.success_score, failure_score=excluded.failure_score, last_success_at=excluded.last_success_at, last_failure_at=excluded.last_failure_at, updated_at=excluded.updated_at",
                (project_id, peer_agent_id, success_score, failure_score, last_success_at, last_failure_at, timestamp),
            )

    def project_gossip_peer_metric(self, project_id: str, peer_agent_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute(
                "SELECT project_id, peer_agent_id, success_score, failure_score, last_success_at, last_failure_at, updated_at FROM project_gossip_peer_metrics WHERE project_id = ? AND peer_agent_id = ?",
                (project_id, peer_agent_id),
            ).fetchone()
        return dict(row) if row else None

    def project_gossip_attempts(self, project_id: str, peer_agent_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT attempt_id, success, recorded_at FROM project_gossip_attempts WHERE project_id = ? AND peer_agent_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
                (project_id, peer_agent_id, min(max(int(limit), 1), 100)),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_project_board_sent_events(self, project_id: str, peer_agent_id: str, event_ids: List[str]) -> None:
        current = self.project_board_sent_event_ids(project_id, peer_agent_id)
        current.update(str(item) for item in event_ids)
        # Keep a bounded propagation window. The event log itself is durable;
        # this set only prevents repeated forwarding loops and does not act as
        # the source of truth for board recovery.
        if len(current) > 5000:
            current = set(sorted(current)[-5000:])
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO project_board_sync_state(project_id, peer_agent_id, event_ids_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(project_id, peer_agent_id) DO UPDATE SET event_ids_json=excluded.event_ids_json, updated_at=excluded.updated_at",
                (project_id, peer_agent_id, json.dumps(sorted(current), separators=(",", ":")), int(time.time())),
            )

    def reset_project_board_sent_events(self, project_id: str, peer_agent_id: str = "") -> None:
        with self.lock, self.db:
            if peer_agent_id:
                self.db.execute("DELETE FROM project_board_sync_state WHERE project_id = ? AND peer_agent_id = ?", (project_id, peer_agent_id))
            else:
                self.db.execute("DELETE FROM project_board_sync_state WHERE project_id = ?", (project_id,))

    def project_peer(self, project_id: str, agent_id: str) -> Optional[PublicIdentity]:
        with self.lock:
            row = self.db.execute("SELECT pp.identity_json FROM project_peers pp JOIN projects p ON p.project_id = pp.project_id WHERE pp.project_id = ? AND pp.agent_id = ? AND pp.revoked_at IS NULL AND p.status = 'active'", (project_id, agent_id)).fetchone()
        return PublicIdentity.from_dict(json.loads(row["identity_json"])) if row else None

    def close_project(self, project_id: str) -> Dict[str, Any]:
        now = int(time.time())
        with self.lock, self.db:
            project = self.db.execute("SELECT status FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            if not project:
                raise ValueError("project not found")
            if project["status"] != "closed":
                self.db.execute("UPDATE projects SET status = 'closed', closed_at = ? WHERE project_id = ?", (now, project_id))
                self.db.execute("UPDATE project_peers SET revoked_at = COALESCE(revoked_at, ?) WHERE project_id = ?", (now, project_id))
                self.db.execute("UPDATE project_members SET revoked_at = COALESCE(revoked_at, ?) WHERE project_id = ?", (now, project_id))
        return self.project(project_id) or {}

    def claim_fl_round(self, round_id: str, nonce: str, model_hash: str, config: Optional[Dict[str, Any]] = None) -> bool:
        config_json = json.dumps(config or {}, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            existing = self.db.execute("SELECT nonce, model_hash, config_json FROM fl_rounds WHERE round_id = ?", (round_id,)).fetchone()
            if existing:
                if existing["nonce"] != nonce or existing["model_hash"] != model_hash:
                    raise ValueError("round replay or nonce/model mismatch")
                return False
            self.db.execute("INSERT INTO fl_rounds(round_id, nonce, model_hash, status, created_at, config_json) VALUES (?, ?, ?, 'claimed', ?, ?)", (round_id, nonce, model_hash, int(time.time()), config_json))
            return True

    def mark_fl_round(self, round_id: str, status: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE fl_rounds SET status = ? WHERE round_id = ?", (status, round_id))

    def fl_round(self, round_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute("SELECT round_id, nonce, model_hash, status, created_at, config_json FROM fl_rounds WHERE round_id = ?", (round_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["config"] = json.loads(value.pop("config_json") or "{}")
        return value

    def fl_rounds(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT round_id, nonce, model_hash, status, created_at, config_json FROM fl_rounds ORDER BY created_at DESC LIMIT ?", (min(max(int(limit), 1), 500),)).fetchall()
        output = []
        for row in rows:
            value = dict(row)
            value["config"] = json.loads(value.pop("config_json") or "{}")
            output.append(value)
        return output

    def fl_recoveries(self, round_id: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        with self.lock:
            if round_id:
                rows = self.db.execute("SELECT recovery_id, round_id, survivor_id, missing_id, mask_hash, received_at FROM fl_recoveries WHERE round_id = ? ORDER BY received_at DESC LIMIT ?", (round_id, min(max(int(limit), 1), 1000))).fetchall()
            else:
                rows = self.db.execute("SELECT recovery_id, round_id, survivor_id, missing_id, mask_hash, received_at FROM fl_recoveries ORDER BY received_at DESC LIMIT ?", (min(max(int(limit), 1), 1000),)).fetchall()
        return [dict(row) for row in rows]

    def record_fl_update(self, update_id: str, round_id: str, node_id: str, model_hash: str) -> bool:
        try:
            with self.lock, self.db:
                self.db.execute("INSERT INTO fl_updates(update_id, round_id, node_id, model_hash, received_at) VALUES (?, ?, ?, ?, ?)", (update_id, round_id, node_id, model_hash, int(time.time())))
            return True
        except sqlite3.IntegrityError:
            return False

    def record_fl_recovery(self, recovery_id: str, round_id: str, survivor_id: str, missing_id: str, mask_hash: str) -> bool:
        if not recovery_id:
            return False
        try:
            with self.lock, self.db:
                self.db.execute("INSERT INTO fl_recoveries(recovery_id, round_id, survivor_id, missing_id, mask_hash, received_at) VALUES (?, ?, ?, ?, ?, ?)", (recovery_id, round_id, survivor_id, missing_id, mask_hash, int(time.time())))
            return True
        except sqlite3.IntegrityError:
            return False

    def acquire_fl_coordinator_lease(self, owner_id: str, ttl_seconds: float = 30.0) -> bool:
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("SELECT owner_id, expires_at FROM fl_coordinator_lease WHERE lease_id = 'default'").fetchone()
            if row and row["owner_id"] != owner_id and float(row["expires_at"]) > now:
                return False
            self.db.execute("INSERT INTO fl_coordinator_lease(lease_id, owner_id, expires_at) VALUES ('default', ?, ?) ON CONFLICT(lease_id) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at", (owner_id, now + max(1.0, float(ttl_seconds))))
            return True

    def renew_fl_coordinator_lease(self, owner_id: str, ttl_seconds: float = 30.0) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE fl_coordinator_lease SET expires_at = ? WHERE lease_id = 'default' AND owner_id = ?", (time.time() + max(1.0, float(ttl_seconds)), owner_id))
        return cursor.rowcount == 1

    def release_fl_coordinator_lease(self, owner_id: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("DELETE FROM fl_coordinator_lease WHERE lease_id = 'default' AND owner_id = ?", (owner_id,))
        return cursor.rowcount == 1

    def save_fl_coordinator_state(self, model: Dict[str, List[float]], round_index: int, model_hash_value: str) -> None:
        encoded = json.dumps(model, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            self.db.execute("INSERT INTO fl_coordinator_state(state_id, model_json, round_index, model_hash, updated_at) VALUES ('default', ?, ?, ?, ?) ON CONFLICT(state_id) DO UPDATE SET model_json=excluded.model_json, round_index=excluded.round_index, model_hash=excluded.model_hash, updated_at=excluded.updated_at", (encoded, int(round_index), model_hash_value, int(time.time())))

    def fl_coordinator_state(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute("SELECT model_json, round_index, model_hash, updated_at FROM fl_coordinator_state WHERE state_id = 'default'").fetchone()
        if not row:
            return None
        return {"model": json.loads(row["model_json"]), "round_index": row["round_index"], "model_hash": row["model_hash"], "updated_at": row["updated_at"]}

    def record_broadcast_receipt(self, broadcast_id: str) -> bool:
        try:
            with self.lock, self.db:
                self.db.execute("INSERT INTO broadcast_receipts(broadcast_id, received_at) VALUES (?, ?)", (broadcast_id, int(time.time())))
            return True
        except sqlite3.IntegrityError:
            return False

    def add_trace(self, event_type: str, actor_id: str, payload: Any, event_id: Optional[str] = None) -> str:
        import uuid
        event_id = event_id or str(uuid.uuid4())
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO trace_events(event_id, event_type, actor_id, created_at, payload_json) VALUES (?, ?, ?, ?, ?)", (event_id, event_type, actor_id, int(time.time()), json.dumps(payload, ensure_ascii=False, sort_keys=True)))
        return event_id

    def traces(self, limit: int = 100, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.lock:
            if event_type:
                rows = self.db.execute("SELECT event_id, event_type, actor_id, created_at, payload_json FROM trace_events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?", (event_type, min(max(int(limit), 1), 500))).fetchall()
            else:
                rows = self.db.execute("SELECT event_id, event_type, actor_id, created_at, payload_json FROM trace_events ORDER BY created_at DESC LIMIT ?", (min(max(int(limit), 1), 500),)).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "created_at": row["created_at"], "payload": json.loads(row["payload_json"])} for row in rows]

    def install_bootstrap(self, manifest: Dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO bootstrap_state(state_id, manifest_json, installed_at) VALUES ('default', ?, ?) ON CONFLICT(state_id) DO UPDATE SET manifest_json=excluded.manifest_json, installed_at=excluded.installed_at", (json.dumps(manifest, ensure_ascii=False, sort_keys=True), int(time.time())))

    def bootstrap(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.db.execute("SELECT manifest_json, installed_at FROM bootstrap_state WHERE state_id = 'default'").fetchone()
        return {"manifest": json.loads(row["manifest_json"]), "installed_at": row["installed_at"]} if row else None

    def upsert_skill(self, manifest: Dict[str, Any]) -> None:
        required = ("skill_id", "name", "version")
        if any(not manifest.get(key) for key in required):
            raise ValueError("skill requires skill_id, name and version")
        skill_id = str(manifest["skill_id"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", skill_id):
            raise ValueError("skill_id must contain only letters, numbers, '.', '_' or '-'")
        capabilities = manifest.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
            raise ValueError("skill capabilities must be a list of strings")
        execution = manifest.get("execution", "disabled-by-default")
        if execution not in {"disabled-by-default", "command", "http"}:
            raise ValueError("skill execution must be disabled-by-default, command or http")
        if len(json.dumps(manifest, ensure_ascii=False)) > 256 * 1024:
            raise ValueError("skill manifest is too large")
        manifest = {**manifest, "execution": execution, "enabled": bool(manifest.get("enabled", True))}
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
        with self.lock, self.db:
            self.db.execute("INSERT INTO skills(skill_id, name, version, manifest_json, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(skill_id) DO UPDATE SET name=excluded.name, version=excluded.version, manifest_json=excluded.manifest_json, enabled=excluded.enabled", (skill_id, manifest["name"], manifest["version"], encoded, 1 if manifest.get("enabled", True) else 0, int(time.time())))
        skills_dir = self.data_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / f"{skill_id}.json").write_text(encoded + "\n", "utf-8")

    def skills(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        with self.lock:
            query = "SELECT manifest_json, enabled, created_at FROM skills" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY name, version"
            rows = self.db.execute(query).fetchall()
        output = []
        for row in rows:
            value = json.loads(row["manifest_json"]); value["enabled"] = bool(row["enabled"]); value["created_at"] = row["created_at"]; output.append(value)
        return output

    def next_sequence(self, recipient_id: str) -> int:
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT sequence FROM counters WHERE recipient_id = ?", (recipient_id,)
            ).fetchone()
            value = (row["sequence"] if row else 0) + 1
            self.db.execute(
                "INSERT INTO counters(recipient_id, sequence) VALUES (?, ?) "
                "ON CONFLICT(recipient_id) DO UPDATE SET sequence = excluded.sequence",
                (recipient_id, value),
            )
        return value

    def has_received(self, message_id: str) -> bool:
        with self.lock:
            return self.db.execute(
                "SELECT 1 FROM received WHERE message_id = ?", (message_id,)
            ).fetchone() is not None

    def save_received(self, envelope: Dict[str, Any], body: Dict[str, Any]) -> bool:
        try:
            with self.lock, self.db:
                self.db.execute(
                    """INSERT INTO received(message_id, sender_id, sequence, received_at, body_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        envelope["message_id"],
                        envelope["sender_id"],
                        envelope["sequence"],
                        int(time.time()),
                        json.dumps(body, ensure_ascii=False, sort_keys=True),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_acknowledged(self, message_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE received SET acknowledged = 1 WHERE message_id = ?", (message_id,))
