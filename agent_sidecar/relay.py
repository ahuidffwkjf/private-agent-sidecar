from __future__ import annotations

import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from collections import defaultdict, deque
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse

from .crypto import PublicIdentity, verify_signature
from .broadcast import verify_broadcast
from .bootstrap import BOOTSTRAP_MANIFEST
from .http_utils import JsonHandler, enable_tls
from .protocol import auth_payload, verify_envelope
from .store import RelayStore


def _authorized(store: RelayStore, action: str, body: Dict[str, Any], target: str = "") -> bool:
    try:
        agent_id = body["agent_id"]
        timestamp = int(body["timestamp"])
        signature = body["signature"]
        identity = store.identity(agent_id)
        return bool(
            identity
            and abs(int(time.time()) - timestamp) <= 300
            and verify_signature(identity.signing_key, signature, auth_payload(action, agent_id, timestamp, target))
        )
    except (KeyError, TypeError, ValueError):
        return False


def make_handler(store: RelayStore, access_token: str = "", rate_limit_per_minute: int = 0):
    rate_lock = threading.RLock()
    request_times = defaultdict(deque)

    def require_rate_limit(handler: JsonHandler) -> bool:
        if rate_limit_per_minute <= 0:
            return True
        key = handler.client_address[0]
        now = time.monotonic()
        with rate_lock:
            bucket = request_times[key]
            while bucket and now - bucket[0] >= 60.0:
                bucket.popleft()
            if len(bucket) >= rate_limit_per_minute:
                handler.send_json(429, {"error": "relay_rate_limit_exceeded", "retry_after_seconds": max(1, int(60 - (now - bucket[0])))})
                return False
            bucket.append(now)
        return True

    def require_access(handler: JsonHandler) -> bool:
        if not require_rate_limit(handler):
            return False
        if access_token and handler.headers.get("X-Relay-Token", "") != access_token:
            handler.send_json(401, {"error": "invalid_relay_token"})
            return False
        return True
    class RelayHandler(JsonHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/v1/agents":
                    if not require_access(self):
                        return
                    self.send_json(200, {"agents": [identity.as_dict() for identity in store.identities()]})
                    return
                if parsed.path.startswith("/v1/agents/"):
                    if not require_access(self):
                        return
                    agent_id = unquote(parsed.path.removeprefix("/v1/agents/"))
                    identity = store.identity(agent_id)
                    self.send_json(200, identity.as_dict()) if identity else self.send_json(404, {"error": "not found"})
                    return
                if parsed.path == "/v1/bootstrap":
                    if not require_access(self):
                        return
                    self.send_json(200, BOOTSTRAP_MANIFEST)
                    return
                if parsed.path == "/v1/broadcasts":
                    if not require_access(self):
                        return
                    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                    self.send_json(200, {"broadcasts": store.broadcasts(int(query.get("limit", 100)), query.get("channel"), int(query.get("since", 0)))})
                    return
                if parsed.path == "/v1/messages":
                    if not require_access(self):
                        return
                    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                    authorization = {
                        "agent_id": self.headers.get("X-Agent-Id"),
                        "timestamp": self.headers.get("X-Timestamp"),
                        "signature": self.headers.get("X-Signature"),
                    }
                    if not _authorized(store, "inbox", authorization):
                        self.send_json(401, {"error": "invalid inbox authorization"})
                        return
                    self.send_json(
                        200,
                        {"messages": store.inbox(authorization["agent_id"], int(query.get("limit", 100)))},
                    )
                    return
                if parsed.path == "/healthz":
                    self.send_json(200, {"status": "ok"})
                    return
                self.send_json(404, {"error": "not found"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                body = self.read_json()
                if parsed.path == "/v1/agents":
                    if not require_access(self):
                        return
                    store.register(PublicIdentity.from_dict(body))
                    self.send_json(201, {"agent_id": body["agent_id"]})
                    return
                if parsed.path == "/v1/broadcasts":
                    if not require_access(self):
                        return
                    sender = store.identity(body.get("sender_id", ""))
                    if not sender:
                        self.send_json(403, {"error": "sender is not registered"})
                        return
                    verify_broadcast(body, sender)
                    store.enqueue_broadcast(body)
                    self.send_json(201, {"broadcast_id": body["broadcast_id"]})
                    return
                if parsed.path == "/v1/messages":
                    if not require_access(self):
                        return
                    sender = store.identity(body["sender_id"])
                    if not sender:
                        self.send_json(403, {"error": "sender is not registered"})
                        return
                    if not store.identity(body["recipient_id"]):
                        self.send_json(404, {"error": "recipient is not registered"})
                        return
                    verify_envelope(body, sender)
                    store.enqueue(body)
                    self.send_json(201, {"message_id": body["message_id"]})
                    return
                if parsed.path.startswith("/v1/messages/") and parsed.path.endswith("/ack"):
                    if not require_access(self):
                        return
                    message_id = unquote(parsed.path.removeprefix("/v1/messages/").removesuffix("/ack"))
                    if not _authorized(store, "ack", body, message_id):
                        self.send_json(401, {"error": "invalid acknowledgement authorization"})
                        return
                    changed = store.acknowledge(body["agent_id"], message_id)
                    self.send_json(200 if changed else 404, {"acknowledged": changed})
                    return
                self.send_json(404, {"error": "not found"})
            except sqlite3.IntegrityError:
                self.send_json(409, {"error": "duplicate message"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

    return RelayHandler


def serve_relay(host: str, port: int, database: Path, tls_cert: Optional[Path] = None, tls_key: Optional[Path] = None, access_token: str = "", rate_limit_per_minute: int = 0) -> None:
    store = RelayStore(database)
    server = ThreadingHTTPServer((host, port), make_handler(store, access_token, rate_limit_per_minute))
    enable_tls(server, tls_cert, tls_key)
    scheme = "https" if tls_cert else "http"
    print(f"Relay listening on {scheme}://{host}:{port}")
    server.serve_forever()
