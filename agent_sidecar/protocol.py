from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .crypto import Identity, PublicIdentity, b64d, b64e, canonical, raw_public, verify_signature

PROTOCOL = "agent-message/1"
KDF_INFO = b"private-agent-sidecar/message/v1"


def _headers(envelope: Dict[str, Any]) -> Dict[str, Any]:
    names = (
        "protocol",
        "message_id",
        "sender_id",
        "recipient_id",
        "sequence",
        "created_at",
        "expires_at",
        "ephemeral_key",
        "nonce",
    )
    headers = {name: envelope[name] for name in names}
    if envelope.get("project_id"):
        headers["project_id"] = envelope["project_id"]
    return headers


def signed_bytes(envelope: Dict[str, Any]) -> bytes:
    body = dict(_headers(envelope))
    body["ciphertext"] = envelope["ciphertext"]
    return canonical(body)


def encrypt_message(
    identity: Identity,
    recipient: PublicIdentity,
    sequence: int,
    message_type: str,
    payload: Any,
    ttl_seconds: int = 86400,
    project_id: str = "",
) -> Dict[str, Any]:
    now = int(time.time())
    message_id = str(uuid.uuid4())
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(b64d(recipient.encryption_key)))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=uuid.UUID(message_id).bytes, info=KDF_INFO).derive(shared)
    nonce = os.urandom(12)
    envelope: Dict[str, Any] = {
        "protocol": PROTOCOL,
        "message_id": message_id,
        "sender_id": identity.agent_id,
        "recipient_id": recipient.agent_id,
        "sequence": sequence,
        "created_at": now,
        "expires_at": now + ttl_seconds,
        "ephemeral_key": b64e(raw_public(ephemeral.public_key())),
        "nonce": b64e(nonce),
    }
    if project_id:
        envelope["project_id"] = project_id
    plaintext = canonical({"type": message_type, "payload": payload})
    envelope["ciphertext"] = b64e(ChaCha20Poly1305(key).encrypt(nonce, plaintext, canonical(_headers(envelope))))
    envelope["signature"] = identity.sign(signed_bytes(envelope))
    return envelope


def verify_envelope(envelope: Dict[str, Any], sender: PublicIdentity) -> None:
    if envelope.get("protocol") != PROTOCOL:
        raise ValueError("unsupported protocol")
    if envelope.get("sender_id") != sender.agent_id:
        raise ValueError("sender identity mismatch")
    if not verify_signature(sender.signing_key, envelope["signature"], signed_bytes(envelope)):
        raise ValueError("invalid message signature")


def decrypt_message(identity: Identity, envelope: Dict[str, Any], sender: PublicIdentity) -> Dict[str, Any]:
    verify_envelope(envelope, sender)
    now = int(time.time())
    if envelope["recipient_id"] != identity.agent_id:
        raise ValueError("message addressed to another agent")
    if envelope["expires_at"] < now:
        raise ValueError("message expired")
    if envelope["created_at"] > now + 300:
        raise ValueError("message timestamp is in the future")
    shared = identity.encryption_private.exchange(X25519PublicKey.from_public_bytes(b64d(envelope["ephemeral_key"])))
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=uuid.UUID(envelope["message_id"]).bytes,
        info=KDF_INFO,
    ).derive(shared)
    plaintext = ChaCha20Poly1305(key).decrypt(
        b64d(envelope["nonce"]),
        b64d(envelope["ciphertext"]),
        canonical(_headers(envelope)),
    )
    return json.loads(plaintext.decode("utf-8"))


def auth_payload(action: str, agent_id: str, timestamp: int, target: str = "") -> bytes:
    return canonical({"action": action, "agent_id": agent_id, "timestamp": timestamp, "target": target})
