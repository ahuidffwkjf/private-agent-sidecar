from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from .crypto import Identity, PublicIdentity, canonical, verify_signature

BROADCAST_PROTOCOL = "agent-broadcast/1"


def broadcast_signed_bytes(broadcast: Dict[str, Any]) -> bytes:
    fields = {
        "protocol": broadcast["protocol"],
        "broadcast_id": broadcast["broadcast_id"],
        "sender_id": broadcast["sender_id"],
        "channel": broadcast["channel"],
        "created_at": broadcast["created_at"],
        "expires_at": broadcast["expires_at"],
        "content_type": broadcast["content_type"],
        "content": broadcast["content"],
        "tags": broadcast.get("tags", []),
    }
    return canonical(fields)


def create_broadcast(identity: Identity, channel: str, content: Any, content_type: str = "markdown", tags: list[str] | None = None, ttl_seconds: int = 86400) -> Dict[str, Any]:
    now = int(time.time())
    broadcast: Dict[str, Any] = {
        "protocol": BROADCAST_PROTOCOL,
        "broadcast_id": str(uuid.uuid4()),
        "sender_id": identity.agent_id,
        "channel": channel or "public",
        "created_at": now,
        "expires_at": now + int(ttl_seconds),
        "content_type": content_type,
        "content": content,
        "tags": tags or [],
    }
    broadcast["signature"] = identity.sign(broadcast_signed_bytes(broadcast))
    return broadcast


def verify_broadcast(broadcast: Dict[str, Any], sender: PublicIdentity) -> None:
    if broadcast.get("protocol") != BROADCAST_PROTOCOL:
        raise ValueError("unsupported broadcast protocol")
    if broadcast.get("sender_id") != sender.agent_id:
        raise ValueError("broadcast sender mismatch")
    if not verify_signature(sender.signing_key, broadcast["signature"], broadcast_signed_bytes(broadcast)):
        raise ValueError("invalid broadcast signature")
