from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .crypto import PublicIdentity


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
                CREATE TABLE IF NOT EXISTS peers (
                    agent_id TEXT PRIMARY KEY,
                    identity_json TEXT NOT NULL,
                    trusted_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS counters (
                    recipient_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS received (
                    message_id TEXT PRIMARY KEY,
                    sender_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    body_json TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0
                );
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

    def create_project(self, project_id: str, name: str, owner_id: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", project_id):
            raise ValueError("project_id must contain only letters, numbers, '.', '_' or '-'")
        if not str(name).strip():
            raise ValueError("project name is required")
        now = int(time.time())
        with self.lock, self.db:
            self.db.execute("INSERT INTO projects(project_id, name, owner_id, status, created_at) VALUES (?, ?, ?, 'active', ?)", (project_id, str(name).strip(), owner_id, now))
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

