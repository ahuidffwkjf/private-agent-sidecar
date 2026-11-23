"""Opt-in experimental content collection, isolated from transport Relay."""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from http.server import ThreadingHTTPServer

from .crypto import canonical
from .http_utils import JsonHandler, enable_tls
from .toy_protocol import digest, post, safe_url, sign_request, text, verify_request

SECRET_KEYS = re.compile(r"password|secret|token|api.?key|private.?key|authorization|cookie|signing_private|encryption_private", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEYS.search(k) else redact(v) for k, v in value.items()
                if k not in {"reasoning", "chain_of_thought", "hidden_reasoning"}}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?is)-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----", "[REDACTED]", value)
        value = re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{8,}|bearer\s+[^\s\"']+)", "[REDACTED]", value)
        return re.sub(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", value)
    return value


class ResearchStore:
    def __init__(self, path, experiment_id):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.experiment_id = experiment_id
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS consents(node_id TEXT PRIMARY KEY, active INTEGER, issued_at INTEGER, request_id TEXT, proof TEXT);
                CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL, created_at INTEGER NOT NULL, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS event_subjects(event_id TEXT, node_id TEXT, PRIMARY KEY(event_id,node_id));
                CREATE TABLE IF NOT EXISTS deletions(node_id TEXT PRIMARY KEY, deleted_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            """)
            old = self.db.execute("SELECT value FROM settings WHERE key='experiment'").fetchone()
            if old and old[0] != experiment_id:
                raise ValueError("research database belongs to another experiment")
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('experiment',?)", (experiment_id,))

    def ingest(self, request):
        identity = verify_request(request)
        node, data = identity.agent_id, request["data"]
        if data.get("experiment_id") != self.experiment_id:
            raise ValueError("experiment mismatch")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM deletions WHERE node_id IN (?, '*')", (node,)).fetchone():
                return {"deleted": True, "accepted": []}
            if request["operation"] == "research.consent":
                proof = data["proof"]
                signer = verify_request(proof, "profile", fresh=False)
                if signer.agent_id != node or proof["data"]["experiment_id"] != self.experiment_id or type(proof["data"]["consent_active"]) is not bool:
                    raise ValueError("invalid collection consent")
                old = self.db.execute("SELECT * FROM consents WHERE node_id=?", (node,)).fetchone()
                if old and proof["issued_at"] < old["issued_at"]:
                    raise ValueError("consent rollback")
                # A withdrawal cannot be reversed by replaying an earlier consent.
                if old and not old["active"] and old["request_id"] != proof["request_id"] and proof["issued_at"] <= old["issued_at"]:
                    raise ValueError("new consent must have a later timestamp")
                self.db.execute("INSERT INTO consents VALUES (?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET active=excluded.active,issued_at=excluded.issued_at,request_id=excluded.request_id,proof=excluded.proof",
                    (node, proof["data"]["consent_active"], proof["issued_at"], proof["request_id"], json.dumps(proof)))
                return {"consent_active": proof["data"]["consent_active"]}
            if request["operation"] != "research.batch":
                raise ValueError("unknown research operation")
            consent = self.db.execute("SELECT * FROM consents WHERE node_id=?", (node,)).fetchone()
            if not consent or not consent["active"]:
                raise ValueError("active collection consent required")
            events = data.get("events")
            if not isinstance(events, list) or len(events) > 100:
                raise ValueError("batch limit is 100")
            accepted, dropped = [], []
            for event in events:
                text(event["event_id"], 150)
                if event.get("node_id") != node or event.get("user_id") != node or event.get("experiment_id") != self.experiment_id or event.get("consent_id") != consent["request_id"]:
                    raise ValueError("event identity/consent mismatch")
                if type(event.get("created_at")) is not int or not consent["issued_at"] <= event["created_at"] <= int(time.time()) + 30:
                    raise ValueError("event outside consent period")
                subjects = event.get("subjects", [])
                if not isinstance(subjects, list) or node not in subjects or len(subjects) > 1000:
                    raise ValueError("invalid participants")
                if any(self.db.execute("SELECT 1 FROM deletions WHERE node_id=?", (subject,)).fetchone() for subject in subjects):
                    dropped.append(event["event_id"])
                    continue
                # Content is accepted only when all referenced participants opted in.
                if any(not self.db.execute("SELECT 1 FROM consents WHERE node_id=? AND active=1", (subject,)).fetchone() for subject in subjects):
                    raise ValueError("all content participants must consent")
                encoded = json.dumps(redact(event), sort_keys=True)
                if len(encoded.encode()) > 1024 * 1024:
                    raise ValueError("trace event too large")
                previous = self.db.execute("SELECT record FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
                if previous and previous[0] != encoded:
                    raise ValueError("conflicting trace event")
                self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)", (event["event_id"], node, self.experiment_id, event["created_at"], encoded))
                self.db.executemany("INSERT OR IGNORE INTO event_subjects VALUES (?,?)", [(event["event_id"], s) for s in subjects])
                accepted.append(event["event_id"])
            return {"accepted": accepted, "dropped_deleted": dropped}

    def query(self, node_id=None, after=0, limit=500):
        with self.lock:
            rows = self.db.execute("SELECT rowid,record FROM events WHERE rowid>? AND (? IS NULL OR node_id=?) ORDER BY rowid LIMIT ?", (after, node_id, node_id, min(limit, 1000))).fetchall()
        return {"events": [json.loads(r[1]) for r in rows], "next_cursor": rows[-1][0] if rows else after}

    def delete(self, node_id=None):
        with self.lock, self.db:
            target = node_id or "*"
            self.db.execute("INSERT OR REPLACE INTO deletions VALUES (?,?)", (target, int(time.time())))
            if node_id:
                self.db.execute("DELETE FROM events WHERE event_id IN (SELECT event_id FROM event_subjects WHERE node_id=?)", (node_id,))
                self.db.execute("DELETE FROM consents WHERE node_id=?", (node_id,))
            else:
                self.db.execute("DELETE FROM events")
                self.db.execute("DELETE FROM consents")
            self.db.execute("DELETE FROM event_subjects WHERE event_id NOT IN (SELECT event_id FROM events)")
        return {"deleted": True, "target": target, "reupload_blocked": True}


def make_research_handler(store, admin_token):
    class Handler(JsonHandler):
        def do_GET(self):
            if self.path == "/healthz":
                self.send_json(200, {"status": "ok"})
            else:
                self.send_json(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = self.read_json()
                if self.path == "/v1/research/ingest":
                    result = store.ingest(body)
                elif self.path in {"/v1/research/query", "/v1/research/delete"}:
                    if not hmac.compare_digest(self.headers.get("X-Research-Admin", ""), admin_token):
                        self.send_json(403, {"error": "administrator authorization required"})
                        return
                    result = store.query(**body) if self.path.endswith("query") else store.delete(**body)
                else:
                    self.send_json(404, {"error": "not found"})
                    return
                self.send_json(200, result)
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def log_message(self, *_args):
            pass
    return Handler


class ResearchClient:
    def __init__(self, sidecar, config):
        self.sidecar, self.store, self.config = sidecar, sidecar.store, config
        self.error = None
        with self.store.lock, self.store.db:
            self.store.db.executescript("""
                CREATE TABLE IF NOT EXISTS research_outbox(event_id TEXT PRIMARY KEY, record TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_local(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        self.store.research_client = self

    def consent(self, proof):
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO research_local VALUES ('consent',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(proof),))
            if not proof["data"]["consent_active"]:
                self.store.db.execute("UPDATE research_outbox SET status='withdrawn' WHERE status='queued'")

    def _consent(self):
        with self.store.lock:
            row = self.store.db.execute("SELECT value FROM research_local WHERE key='consent'").fetchone()
        return json.loads(row[0]) if row else None

    def record(self, kind, payload, subjects=None, object_id=None, actor_id=None):
        consent = self._consent()
        if self.config.get("mode") != "toy" or not consent or not consent["data"]["consent_active"]:
            return
        node = self.sidecar.identity.agent_id
        # Referenced people must be indexed too, so deleting a user's data also
        # removes copies of their content embedded in another user's trace.
        references = set()
        def identify(value):
            if isinstance(value, dict):
                for k,v in value.items():
                    identify(k)
                    identify(v)
            elif isinstance(value, list):
                for v in value:
                    identify(v)
            elif isinstance(value, str) and re.fullmatch(r"agent:[a-f0-9]{32}", value):
                references.add(value)
        identify(payload)
        subjects = sorted(set([node] + list(subjects or [])) | references)
        event = {"event_id": uuid.uuid4().hex, "experiment_id": consent["data"]["experiment_id"],
                 "user_id": node, "node_id": node, "actor_id": actor_id, "created_at": int(time.time()),
                 "kind": kind, "subjects": subjects, "object_id": object_id,
                 "consent_id": consent["request_id"], "payload": redact(payload)}
        # Callers can include this insertion in their existing SQLite transaction.
        with self.store.lock:
            self.store.db.execute("INSERT INTO research_outbox VALUES (?,?,'queued')", (event["event_id"], json.dumps(event)))

    def flush(self):
        consent = self._consent()
        if self.config.get("mode") != "toy" or not consent:
            return self.status()
        url = safe_url(self.config["research_url"]) + "/v1/research/ingest"
        experiment = consent["data"]["experiment_id"]
        try:
            result = post(url, sign_request(self.sidecar.identity, "research.consent", {"experiment_id": experiment, "proof": consent}))
            if result.get("deleted"):
                with self.store.lock, self.store.db:
                    self.store.db.execute("DELETE FROM research_outbox")
                self.error = "research records deleted by administrator"
                return self.status()
            if not consent["data"]["consent_active"]:
                return self.status()
            with self.store.lock:
                rows = self.store.db.execute("SELECT record FROM research_outbox WHERE status='queued' ORDER BY rowid LIMIT 100").fetchall()
            if rows:
                events, size = [], 0
                for row in rows:
                    record_size = len(row[0].encode())
                    if events and size + record_size > 1500000:
                        break
                    events.append(json.loads(row[0]))
                    size += record_size
                result = post(url, sign_request(self.sidecar.identity, "research.batch", {"experiment_id": experiment, "events": events}))
                with self.store.lock, self.store.db:
                    for identifier in result.get("accepted", []):
                        self.store.db.execute("UPDATE research_outbox SET status='uploaded',record='{}' WHERE event_id=?", (identifier,))
                    for identifier in result.get("dropped_deleted", []):
                        self.store.db.execute("DELETE FROM research_outbox WHERE event_id=?", (identifier,))
            self.error = None
        except Exception as exc:
            self.error = type(exc).__name__
        return self.status()

    def status(self):
        consent = self._consent()
        with self.store.lock:
            counts = dict(self.store.db.execute("SELECT status,count(*) FROM research_outbox GROUP BY status").fetchall())
        return {"mode": self.config.get("mode", "standard"), "consent_active": bool(consent and consent["data"]["consent_active"]),
                "experiment_id": consent["data"]["experiment_id"] if consent else self.config.get("experiment_id"),
                "counts": counts, "error": self.error}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--admin-token-file", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (args.tls_cert and args.tls_key):
        parser.error("remote research service requires TLS")
    token = args.admin_token_file.read_text().strip()
    if len(token) < 32:
        parser.error("administrator token must have at least 32 characters")
    store = ResearchStore(args.database, args.experiment)
    server = ThreadingHTTPServer((args.host, args.port), make_research_handler(store, token))
    enable_tls(server, args.tls_cert, args.tls_key)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.db.close()


if __name__ == "__main__":
    main()
