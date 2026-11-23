"""Signed public bounties and explicit, budget-bounded delegation trees.

Business relays organize work; transport Relay only distributes signed events.
Contract consent never changes the global peer allowlist or project membership.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time

from .broadcast import broadcast_signed_bytes, create_broadcast, verify_broadcast
from .crypto import canonical

CHANNEL = "sidecar.bounty.v1"
SCHEMA = "sidecar-bounty/1"
MAX_REWARD = 10**12


def text(value, name, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid " + name)
    return value


def integer(value, name, low=0, high=MAX_REWARD):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid " + name)
    return value


def boolean(value):
    if type(value) is not bool:
        raise ValueError("expected boolean")
    return value


FIELDS = {
    "publish": {"title", "task", "reward", "unit", "acceptance", "deadline", "max_depth"},
    "apply": {"bounty_id", "proposal"},
    "offer": {"bounty_id", "parent_id", "child_id", "child_key", "task", "reward", "acceptance", "allow_relay"},
    "respond": {"offer_id", "decision", "parent_key", "role"},
    "promote": {"offer_id"},
    "revoke": {"offer_id", "reason"},
    "submit": {"offer_id", "artifact_hash"},
    "review": {"offer_id", "submission_id", "decision", "reason"},
    "close": {"bounty_id", "reason"},
}


class Ledger:
    def __init__(self):
        self.bounties = {}
        self.offers = {}
        self.applications = {}

    def active(self, offer_id):
        offer = self.offers[offer_id]
        bounty = self.bounties[offer["bounty_id"]]
        if bounty["status"] != "open" or offer["status"] not in {"accepted", "submitted"}:
            return False
        return not offer["parent_id"] or self.active(offer["parent_id"])

    def _open(self, bounty_id, now):
        if bounty_id not in self.bounties:
            raise ValueError("unknown bounty")
        bounty = self.bounties[bounty_id]
        if bounty["status"] != "open" or now >= bounty["deadline"]:
            raise ValueError("bounty is closed or expired")
        return bounty

    def reserved(self, bounty_id, parent_id):
        # Accepted obligations are never released by cancellation or expiry.
        return sum(o["reward"] for o in self.offers.values()
                   if o["bounty_id"] == bounty_id and o["parent_id"] == parent_id
                   and (o["status"] == "pending" or o.get("consented", False)))

    def apply(self, event, signer_key, now):
        text(event.get("broadcast_id"), "bounty event ID", 128)
        content = event.get("content")
        if not isinstance(content, dict) or set(content) != {"schema", "action", "data"} or content["schema"] != SCHEMA:
            raise ValueError("invalid bounty event schema")
        action, data = content["action"], content["data"]
        if action not in FIELDS or not isinstance(data, dict) or set(data) != FIELDS[action]:
            raise ValueError("unexpected bounty event fields")
        identifier, sender = event["broadcast_id"], event["sender_id"]
        if action == "publish":
            text(data["title"], "title", 200)
            text(data["task"], "task")
            text(data["acceptance"], "acceptance criteria")
            text(data["unit"], "reward unit", 32)
            integer(data["reward"], "reward", 1)
            integer(data["max_depth"], "max depth", 1, 8)
            integer(data["deadline"], "deadline", now + 1, now + 30 * 86400)
            if event["expires_at"] != data["deadline"]:
                raise ValueError("deadline must match signed expiry")
            self.bounties[identifier] = dict(data, bounty_id=identifier, publisher_id=sender,
                                            publisher_key=signer_key, status="open")
            return
        if action in {"apply", "offer", "close"}:
            bounty_id = data["bounty_id"]
        else:
            if data["offer_id"] not in self.offers:
                raise ValueError("unknown offer")
            bounty_id = self.offers[data["offer_id"]]["bounty_id"]
        bounty = self._open(bounty_id, now)
        if event["expires_at"] != bounty["deadline"]:
            raise ValueError("event deadline differs from bounty")
        if action == "apply":
            text(data["proposal"], "proposal")
            if sender == bounty["publisher_id"]:
                raise ValueError("publisher cannot apply to itself")
            self.applications.setdefault(bounty_id, {})[sender] = {"proposal": data["proposal"], "event_id": identifier}
        elif action == "offer":
            child = text(data["child_id"], "child ID", 100)
            text(data["child_key"], "child signing key", 100)
            text(data["task"], "subtask")
            text(data["acceptance"], "acceptance criteria")
            integer(data["reward"], "reward", 1)
            boolean(data["allow_relay"])
            parent_id = data["parent_id"]
            if not isinstance(parent_id, str):
                raise ValueError("invalid parent contract")
            if parent_id:
                parent = self.offers.get(parent_id)
                if not parent or parent["bounty_id"] != bounty_id or parent["child_id"] != sender or parent["status"] != "accepted" or parent["role"] != "relay" or not parent["allow_relay"] or not self.active(parent_id):
                    raise ValueError("parent is not an active authorized business relay")
                ancestors, budget = parent["ancestors"] + [sender], parent["reward"]
            else:
                if sender != bounty["publisher_id"]:
                    raise ValueError("only publisher can make root offers")
                if child not in self.applications.get(bounty_id, {}):
                    raise ValueError("root organizer must apply voluntarily first")
                ancestors, budget = [sender], bounty["reward"]
                if not data["allow_relay"]:
                    raise ValueError("root organizer must be allowed to relay")
            if child in ancestors or len(ancestors) > bounty["max_depth"]:
                raise ValueError("cycle or depth limit")
            if self.reserved(bounty_id, parent_id) + data["reward"] > budget:
                raise ValueError("delegation exceeds unreserved reward budget")
            self.offers[identifier] = dict(data, offer_id=identifier, parent_node_id=sender,
                parent_key=signer_key, ancestors=ancestors, depth=len(ancestors), status="pending",
                consented=False, role=None, submissions=[], reviews=[])
        elif action == "close":
            if sender != bounty["publisher_id"]:
                raise ValueError("only publisher can close a bounty")
            text(data["reason"], "reason")
            bounty["status"] = "closed"
        else:
            offer = self.offers[data["offer_id"]]
            is_parent = sender == offer["parent_node_id"]
            is_child = sender == offer["child_id"]
            if not is_parent and not is_child:
                raise ValueError("not a party to this contract")
            parent_active = not offer["parent_id"] or self.active(offer["parent_id"])
            if action == "respond":
                if not is_child or signer_key != offer["child_key"] or data["parent_key"] != offer["parent_key"]:
                    raise ValueError("explicit bilateral key confirmation failed")
                if offer["status"] != "pending" or not parent_active:
                    raise ValueError("offer is no longer pending or parent was revoked")
                if data["decision"] not in {"accept", "reject"} or data["role"] not in {"node", "relay"}:
                    raise ValueError("invalid decision or role")
                if data["decision"] == "accept":
                    if data["role"] == "relay" and not offer["allow_relay"]:
                        raise ValueError("contract forbids subdelegation")
                    if not offer["parent_id"] and data["role"] != "relay":
                        raise ValueError("root applicant must accept as relay")
                    if any(o["bounty_id"] == bounty_id and o["child_id"] == sender and o["consented"] for o in self.offers.values()):
                        raise ValueError("node already has a parent in this bounty")
                    offer.update(status="accepted", consented=True, role=data["role"], acceptance_id=identifier)
                else:
                    offer["status"] = "rejected"
            elif action == "promote":
                if not is_child or not offer["allow_relay"] or offer["status"] != "accepted" or not self.active(offer["offer_id"]):
                    raise ValueError("contract cannot activate relay role")
                offer["role"] = "relay"
            elif action == "revoke":
                text(data["reason"], "reason")
                if offer["status"] not in {"pending", "accepted", "submitted"}:
                    raise ValueError("contract already terminal")
                offer["status"] = "revoked"
            elif action == "submit":
                digest = data["artifact_hash"]
                if not isinstance(digest, str) or len(digest) != 71 or not digest.startswith("sha256:") or any(c not in "0123456789abcdef" for c in digest[7:]):
                    raise ValueError("result must reference a SHA-256 artifact hash")
                if not is_child or offer["status"] != "accepted" or not self.active(offer["offer_id"]):
                    raise ValueError("contract cannot submit")
                if any(o["parent_id"] == offer["offer_id"] and o["status"] in {"pending", "accepted", "submitted"} for o in self.offers.values()):
                    raise ValueError("resolve outstanding child work before submitting")
                offer["submissions"].append({"submission_id": identifier, "artifact_hash": digest})
                offer["status"] = "submitted"
            elif action == "review":
                if not is_parent or offer["status"] != "submitted" or not self.active(offer["offer_id"]):
                    raise ValueError("only parent can review active submitted work")
                if data["submission_id"] != offer["submissions"][-1]["submission_id"] or data["decision"] not in {"accept", "reject"}:
                    raise ValueError("invalid or stale review")
                text(data["reason"], "review reason")
                offer["reviews"].append(dict(data, event_id=identifier))
                offer["status"] = "completed" if data["decision"] == "accept" else "accepted"

    def snapshot(self, now):
        bounties = copy.deepcopy(list(self.bounties.values()))
        offers = copy.deepcopy(list(self.offers.values()))
        for bounty in bounties:
            if bounty["status"] == "open" and now >= bounty["deadline"]:
                bounty["status"] = "expired"
            bounty["reserved_reward"] = self.reserved(bounty["bounty_id"], "")
        for offer in offers:
            bounty = self.bounties[offer["bounty_id"]]
            offer["effective_active"] = self.active(offer["offer_id"]) and now < bounty["deadline"]
            offer["reserved_reward"] = self.reserved(offer["bounty_id"], offer["offer_id"])
            offer["retained_reward"] = offer["reward"] - offer["reserved_reward"]
            offer["payment_status"] = "not_implemented"
        return {"bounties": bounties, "offers": offers, "applications": copy.deepcopy(self.applications)}


class BountyService:
    def __init__(self, sidecar):
        self.sidecar = sidecar
        self.store = sidecar.store
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.last_sync_error = None
        with self.store.lock, self.store.db:
            self.store.db.executescript("""
                CREATE TABLE IF NOT EXISTS bounty_events (
                    seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                    event_json TEXT NOT NULL, signer_key TEXT NOT NULL,
                    received_at INTEGER NOT NULL, valid INTEGER NOT NULL, error TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bounty_cursor (
                    id INTEGER PRIMARY KEY CHECK(id=1), feed_id TEXT NOT NULL, seq INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bounty_outbox (
                    request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    event_json TEXT NOT NULL, status TEXT NOT NULL
                );
            """)

    def start(self):
        if self.worker is not None:
            raise RuntimeError("bounty polling already started")
        self.stop_event.clear()

        def poll():
            while not self.stop_event.is_set():
                try:
                    self.sync()
                    self.last_sync_error = None
                except Exception as exc:
                    self.last_sync_error = type(exc).__name__
                self.stop_event.wait(5)

        self.worker = threading.Thread(target=poll, name="sidecar-bounties", daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join()
            self.worker = None

    def _ledger(self):
        ledger = Ledger()
        with self.store.lock:
            rows = self.store.db.execute("SELECT * FROM bounty_events WHERE valid=1 ORDER BY seq").fetchall()
        for row in rows:
            ledger.apply(json.loads(row["event_json"]), row["signer_key"], row["received_at"])
        return ledger

    def sync(self):
        with self.lock:
            ledger = self._ledger()
            with self.store.lock:
                row = self.store.db.execute("SELECT * FROM bounty_cursor WHERE id=1").fetchone()
            cursor, feed_id = (row["seq"], row["feed_id"]) if row else (0, "")
            rejected, count = [], 0
            for _ in range(20):
                if self.stop_event.is_set():
                    return {"received": count, "cursor": cursor, "rejected": rejected, "caught_up": False}
                page = self.sidecar.relay.bounty_feed(cursor)
                if feed_id and page["feed_id"] != feed_id:
                    raise ValueError("bounty feed identity changed; explicit resynchronization required")
                feed_id = page["feed_id"]
                for entry in page["events"]:
                    if type(entry["seq"]) is not int or entry["seq"] <= cursor:
                        raise ValueError("bounty feed cursor rollback")
                    event = entry["event"]
                    # Directory/network failures must not advance the cursor.
                    peer = self.sidecar.relay.identity_for(event["sender_id"])
                    valid, error = True, ""
                    try:
                        verify_broadcast(event, peer)
                        if event["channel"] != CHANNEL:
                            raise ValueError("wrong bounty channel")
                        integer(entry["received_at"], "receipt time", 0, int(time.time()) + 30)
                        integer(event["created_at"], "event time", entry["received_at"] - 300, entry["received_at"] + 30)
                        trial = copy.deepcopy(ledger)
                        trial.apply(event, peer.signing_key, entry["received_at"])
                        ledger = trial
                    except (ValueError, KeyError, TypeError):
                        valid, error = False, "invalid signature, schema or transition"
                        rejected.append(event.get("broadcast_id"))
                    with self.store.lock, self.store.db:
                        self.store.db.execute("INSERT INTO bounty_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (entry["seq"], event["broadcast_id"], json.dumps(event), peer.signing_key, entry["received_at"], valid, error))
                        self.store.db.execute("INSERT INTO bounty_cursor VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET seq=excluded.seq", (feed_id, entry["seq"]))
                    cursor = entry["seq"]
                    count += 1
                if not page["has_more"]:
                    return {"received": count, "cursor": cursor, "rejected": rejected, "caught_up": True}
            return {"received": count, "cursor": cursor, "rejected": rejected, "caught_up": False}

    def snapshot(self):
        with self.lock:
            value = self._ledger().snapshot(int(time.time()))
            with self.store.lock:
                value["outbox"] = [{"request_id": r[0], "status": r[1]} for r in self.store.db.execute("SELECT request_id,status FROM bounty_outbox ORDER BY rowid DESC LIMIT 100")]
            value["local_node_id"] = self.sidecar.identity.agent_id
            value["last_sync_error"] = self.last_sync_error
            return value

    def _role_check(self, action, data):
        if action in {"apply", "offer", "promote"}:
            self.sidecar.decisions.require_active("relay")
        if action == "respond" and data.get("decision") == "accept":
            self.sidecar.decisions.require_active(data.get("role"))
        peer_id = data.get("child_id") if action == "offer" else None
        if action == "respond" and data.get("decision") == "accept":
            peer_id = self._ledger().offers.get(data.get("offer_id"), {}).get("parent_node_id")
        if peer_id and any(p["agent_id"] == peer_id and p["trust_state"] == "blocked" for p in self.store.peers()):
            raise ValueError("peer is locally blocked")

    def act(self, action, data, request_id):
        text(request_id, "request ID", 128)
        if not isinstance(action, str) or action not in FIELDS or not isinstance(data, dict) or set(data) != FIELDS[action]:
            raise ValueError("unexpected bounty action fields")
        digest = hashlib.sha256(canonical({"action": action, "data": data})).hexdigest()
        with self.lock:
            with self.store.lock:
                previous = self.store.db.execute("SELECT * FROM bounty_outbox WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise ValueError("request ID already used for different content")
                if previous["status"] in {"applied", "rejected", "expired"}:
                    return {"event_id": json.loads(previous["event_json"])["broadcast_id"], "status": previous["status"]}
                return self._deliver(request_id, json.loads(previous["event_json"]))
            if not self.sync()["caught_up"]:
                raise ValueError("finish synchronizing the bounty feed before acting")
            self._role_check(action, data)
            with self.store.lock:
                pending = self.store.db.execute("SELECT request_id FROM bounty_outbox WHERE status='queued' LIMIT 1").fetchone()
            if pending:
                raise ValueError("retry pending request before creating another: " + pending[0])
            ledger, now = self._ledger(), int(time.time())
            if action == "publish":
                deadline = integer(data.get("deadline"), "deadline", now + 1, now + 30 * 86400)
            else:
                bounty_id = data.get("bounty_id") or ledger.offers.get(data.get("offer_id"), {}).get("bounty_id")
                deadline = ledger._open(bounty_id, now)["deadline"]
            if action == "offer":
                peer = self.sidecar.relay.identity_for(data.get("child_id", ""))
                if peer.signing_key != data.get("child_key"):
                    raise ValueError("child key must be explicitly verified")
            event = create_broadcast(self.sidecar.identity, CHANNEL,
                {"schema": SCHEMA, "action": action, "data": data}, "application/json", ["bounty"], deadline - now)
            event["expires_at"] = deadline
            event["signature"] = self.sidecar.identity.sign(broadcast_signed_bytes(event))
            ledger.apply(event, self.sidecar.identity.public().signing_key, now)
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT INTO bounty_outbox VALUES (?, ?, ?, 'queued')", (request_id, digest, json.dumps(event)))
            return self._deliver(request_id, event)

    def _deliver(self, request_id, event):
        # Retry exactly the same signed event; transport receipt alone is not consent.
        try:
            caught_up = self.sync()["caught_up"]
            with self.store.lock:
                row = self.store.db.execute("SELECT valid FROM bounty_events WHERE event_id=?", (event["broadcast_id"],)).fetchone()
            if row:
                status = "applied" if row[0] else "rejected"
            elif int(time.time()) >= min(event["expires_at"], event["created_at"] + 300):
                status = "expired"
            elif not caught_up:
                status = "queued"
            else:
                # Changed roles/parent contracts invalidate unsent queued work.
                try:
                    self._role_check(event["content"]["action"], event["content"]["data"])
                    self._ledger().apply(event, self.sidecar.identity.public().signing_key, int(time.time()))
                except ValueError:
                    status = "rejected"
                    with self.store.lock, self.store.db:
                        self.store.db.execute("UPDATE bounty_outbox SET status=? WHERE request_id=?", (status, request_id))
                    return {"event_id": event["broadcast_id"], "status": status}
                self.sidecar.relay.publish_bounty_event(event)
                caught_up = self.sync()["caught_up"]
                with self.store.lock:
                    row = self.store.db.execute("SELECT valid FROM bounty_events WHERE event_id=?", (event["broadcast_id"],)).fetchone()
                status = ("applied" if row[0] else "rejected") if row else "queued"
                if not caught_up:
                    status = "queued"
        except (OSError, RuntimeError):
            return {"event_id": event["broadcast_id"], "status": "queued"}
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE bounty_outbox SET status=? WHERE request_id=?", (status, request_id))
        return {"event_id": event["broadcast_id"], "status": status}
