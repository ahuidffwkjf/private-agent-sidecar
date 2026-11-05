from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, Iterable

from .crypto import Identity, PublicIdentity, canonical, verify_signature
from .project_keys import decrypt_content, encrypt_content

BOARD_PROTOCOL = "project-board/1"
BOARD_EVENT_TYPES = {
    "post",
    "reply",
    "edit",
    "delete",
    "announcement",
    "proposal",
    "task",
    "decision",
    "member_joined",
    "member_left",
    "member_removed",
    "membership_updated",
    "board_archived",
}


def participant_set_hash(manifest: Dict[str, Any]) -> str:
    members = []
    for member in manifest.get("members") or []:
        members.append(
            {
                "agent_id": str(member.get("agent_id") or ""),
                "signing_key": str(member.get("signing_key") or ""),
                "encryption_key": str(member.get("encryption_key") or ""),
                "role": str(member.get("role") or "member"),
            }
        )
    members.sort(key=lambda item: item["agent_id"])
    return "sha256:" + hashlib.sha256(canonical(members)).hexdigest()


def signed_board_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in event.items() if key != "signature"}


def create_board_event(
    identity: Identity,
    project_id: str,
    manifest: Dict[str, Any],
    logical_clock: int,
    event_type: str,
    content: Any,
    parent_event_id: str = "",
    content_type: str = "application/json",
    membership_signature: str = "",
    content_key: bytes | None = None,
) -> Dict[str, Any]:
    if event_type not in BOARD_EVENT_TYPES:
        raise ValueError("unsupported project board event type")
    epoch = int(manifest.get("epoch") or 0)
    if epoch <= 0:
        raise ValueError("project membership manifest is unavailable")
    members = {str(member.get("agent_id") or "") for member in manifest.get("members") or []}
    if identity.agent_id not in members:
        raise ValueError("local node is not a current project member")
    event: Dict[str, Any] = {
        "protocol": BOARD_PROTOCOL,
        "event_id": str(uuid.uuid4()),
        "project_id": project_id,
        "board_id": f"board:{project_id}",
        "membership_epoch": epoch,
        "participant_set_hash": participant_set_hash(manifest),
        "author_agent_id": identity.agent_id,
        "logical_clock": int(logical_clock),
        "created_at": int(time.time()),
        "event_type": event_type,
        "parent_event_id": str(parent_event_id or ""),
        "content_type": str(content_type or "application/json"),
        "content": encrypt_content(content_key, content) if content_key else content,
    }
    if membership_signature:
        event["membership_proof"] = {
            "manifest": manifest,
            "signature": membership_signature,
        }
    event["content_hash"] = "sha256:" + hashlib.sha256(canonical(content)).hexdigest()
    event["signature"] = identity.sign(canonical(signed_board_event(event)))
    return event


def verify_board_event(
    event: Dict[str, Any],
    project_id: str,
    manifest: Dict[str, Any],
    content_key: bytes | None = None,
) -> PublicIdentity:
    required = {
        "protocol",
        "event_id",
        "project_id",
        "board_id",
        "membership_epoch",
        "participant_set_hash",
        "author_agent_id",
        "logical_clock",
        "created_at",
        "event_type",
        "content_type",
        "content",
        "content_hash",
        "signature",
        "membership_proof",
    }
    if not isinstance(event, dict) or not required.issubset(event):
        raise ValueError("malformed project board event")
    if event.get("protocol") != BOARD_PROTOCOL:
        raise ValueError("unsupported project board protocol")
    if event.get("project_id") != project_id or event.get("board_id") != f"board:{project_id}":
        raise ValueError("project board event scope mismatch")
    if event.get("event_type") not in BOARD_EVENT_TYPES:
        raise ValueError("unsupported project board event type")
    if int(event.get("logical_clock") or 0) <= 0:
        raise ValueError("invalid project board logical clock")
    if int(event.get("created_at") or 0) > int(time.time()) + 300:
        raise ValueError("project board event timestamp is in the future")
    proof = event.get("membership_proof") or {}
    proof_manifest = proof.get("manifest") or {}
    proof_signature = str(proof.get("signature") or "")
    if proof_manifest != manifest:
        raise ValueError("project board membership proof does not match the selected epoch")
    owner_entry = next((member for member in manifest.get("members") or [] if member.get("agent_id") == manifest.get("owner_id") and member.get("role") == "owner"), None)
    if not owner_entry or not verify_signature(owner_entry["signing_key"], proof_signature, canonical(manifest)):
        raise ValueError("invalid project board membership proof")
    if int(event.get("membership_epoch") or 0) != int(manifest.get("epoch") or 0):
        raise ValueError("project board event uses a stale membership epoch")
    if event.get("participant_set_hash") != participant_set_hash(manifest):
        raise ValueError("project board participant set hash mismatch")
    stored_content = event.get("content")
    if isinstance(stored_content, dict) and stored_content.get("protocol") == "project-content-key/1":
        if content_key is None:
            raise ValueError("project board content key is unavailable")
        try:
            plaintext_content = decrypt_content(content_key, stored_content)
        except Exception as exc:
            raise ValueError("project board content decryption failed") from exc
    else:
        plaintext_content = stored_content
    expected_content_hash = "sha256:" + hashlib.sha256(canonical(plaintext_content)).hexdigest()
    if event.get("content_hash") != expected_content_hash:
        raise ValueError("project board content hash mismatch")
    identities: Dict[str, PublicIdentity] = {}
    for member in manifest.get("members") or []:
        identity = PublicIdentity.from_dict(member)
        identities[identity.agent_id] = identity
    author = identities.get(str(event.get("author_agent_id") or ""))
    if not author:
        raise ValueError("project board author is not a current member")
    if not verify_signature(author.signing_key, str(event.get("signature") or ""), canonical(signed_board_event(event))):
        raise ValueError("invalid project board event signature")
    return author


def decrypt_board_event_content(event: Dict[str, Any], content_key: bytes | None) -> Any:
    stored_content = event.get("content")
    if isinstance(stored_content, dict) and stored_content.get("protocol") == "project-content-key/1":
        if content_key is None:
            return None
        return decrypt_content(content_key, stored_content)
    return stored_content


def event_set_root(events: Iterable[Dict[str, Any]]) -> str:
    identifiers = sorted(str(event.get("event_id") or "") for event in events)
    if not identifiers:
        return "sha256:" + hashlib.sha256(b"").hexdigest()
    level = [hashlib.sha256(canonical(identifier)).digest() for identifier in identifiers]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(level[index] + level[index + 1]).digest()
            for index in range(0, len(level), 2)
        ]
    return "sha256:" + level[0].hex()


def event_bucket(event_id: str, bucket_width: int = 2) -> str:
    if bucket_width <= 0 or bucket_width > 64:
        raise ValueError("bucket width must be between 1 and 64")
    return hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()[:bucket_width]


def event_buckets(events: Iterable[Dict[str, Any]], bucket_width: int = 2) -> list[Dict[str, Any]]:
    grouped: Dict[str, list[str]] = {}
    for event in events:
        event_id = str(event.get("event_id") or "")
        bucket = event_bucket(event_id, bucket_width)
        grouped.setdefault(bucket, []).append(event_id)
    return [
        {
            "bucket": bucket,
            "count": len(event_ids),
            "root": event_set_root({"event_id": event_id} for event_id in sorted(event_ids)),
        }
        for bucket, event_ids in sorted(grouped.items())
    ]
