from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, Optional

from .crypto import Identity, PublicIdentity, canonical, verify_signature

TRUST_STATEMENT_SCHEMA = "node-trust/1"
TRUST_DECISIONS = {"trust", "reject", "block", "revoke"}


def signing_key_fingerprint(signing_key: str) -> str:
    return "sha256:" + hashlib.sha256(signing_key.encode("ascii")).hexdigest()


def signed_trust_statement(statement: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in statement.items() if key != "signature"}


def create_trust_statement(identity: Identity, subject: PublicIdentity, decision: str, issuer_direct: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = str(decision or "").lower()
    if normalized not in TRUST_DECISIONS:
        raise ValueError("unsupported node trust decision")
    now = int(time.time())
    statement: Dict[str, Any] = {
        "schema": TRUST_STATEMENT_SCHEMA,
        "statement_id": str(uuid.uuid4()),
        "issuer_agent_id": identity.agent_id,
        "subject_agent_id": subject.agent_id,
        "issuer_signing_key_fingerprint": signing_key_fingerprint(identity.public().signing_key),
        "subject_signing_key_fingerprint": signing_key_fingerprint(subject.signing_key),
        "decision": normalized,
        "issued_at": now,
        "nonce": str(uuid.uuid4()),
    }
    if issuer_direct:
        endpoint = str(issuer_direct.get("endpoint") or "").rstrip("/")
        if endpoint:
            statement["issuer_direct"] = {
                "endpoint": endpoint,
                "tls_fingerprint": str(issuer_direct.get("tls_fingerprint") or "").lower().replace(":", ""),
                "protocol": str(issuer_direct.get("protocol") or "sidecar-direct/1"),
            }
    statement["signature"] = identity.sign(canonical(statement))
    return statement


def verify_trust_statement(
    statement: Dict[str, Any],
    issuer: PublicIdentity,
    subject: PublicIdentity,
) -> None:
    required = {
        "schema",
        "statement_id",
        "issuer_agent_id",
        "subject_agent_id",
        "issuer_signing_key_fingerprint",
        "subject_signing_key_fingerprint",
        "decision",
        "issued_at",
        "nonce",
        "signature",
    }
    if not isinstance(statement, dict) or not required.issubset(statement):
        raise ValueError("malformed node trust statement")
    if statement.get("schema") != TRUST_STATEMENT_SCHEMA:
        raise ValueError("unsupported node trust statement schema")
    if statement.get("issuer_agent_id") != issuer.agent_id:
        raise ValueError("node trust statement issuer mismatch")
    if statement.get("subject_agent_id") != subject.agent_id:
        raise ValueError("node trust statement subject mismatch")
    if statement.get("decision") not in TRUST_DECISIONS:
        raise ValueError("unsupported node trust decision")
    if statement.get("issuer_signing_key_fingerprint") != signing_key_fingerprint(issuer.signing_key):
        raise ValueError("node trust statement issuer key mismatch")
    if statement.get("subject_signing_key_fingerprint") != signing_key_fingerprint(subject.signing_key):
        raise ValueError("node trust statement subject key mismatch")
    issuer_direct = statement.get("issuer_direct")
    if issuer_direct is not None:
        if not isinstance(issuer_direct, dict):
            raise ValueError("malformed node trust direct endpoint")
        endpoint = str(issuer_direct.get("endpoint") or "")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("node trust direct endpoint must be http(s)")
        if str(issuer_direct.get("protocol") or "") != "sidecar-direct/1":
            raise ValueError("unsupported node trust direct protocol")
    issued_at = int(statement.get("issued_at") or 0)
    if issued_at <= 0 or issued_at > int(time.time()) + 300:
        raise ValueError("invalid node trust statement timestamp")
    if not verify_signature(issuer.signing_key, str(statement.get("signature") or ""), canonical(signed_trust_statement(statement))):
        raise ValueError("invalid node trust statement signature")
