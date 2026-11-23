from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from .crypto import Identity, PublicIdentity, canonical, verify_signature
from .project_board import participant_set_hash

INVITATION_SCHEMA = "project-invitation/1"
INVITATION_RESPONSE_SCHEMA = "project-invitation-response/1"
INVITATION_DECISIONS = {"accept", "reject"}


_INVITATION_SIGNED_FIELDS = (
    "schema",
    "invitation_id",
    "project_id",
    "project_name",
    "owner_agent_id",
    "invitee_agent_id",
    "invitation_kind",
    "membership_epoch",
    "participant_set_hash",
    "membership",
    "issued_at",
    "expires_at",
    "nonce",
)
_RESPONSE_SIGNED_FIELDS = (
    "schema",
    "response_id",
    "invitation_id",
    "project_id",
    "owner_agent_id",
    "invitee_agent_id",
    "decision",
    "issued_at",
    "nonce",
)


def _signed_fields(value: Dict[str, Any], fields: tuple[str, ...]) -> Dict[str, Any]:
    return {key: value[key] for key in fields}


def create_project_invitation(
    identity: Identity,
    invitee: PublicIdentity,
    project: Dict[str, Any],
    membership: Dict[str, Any],
    invitation_kind: str,
    ttl_seconds: int = 7 * 86400,
) -> Dict[str, Any]:
    if project.get("owner_id") != identity.agent_id:
        raise ValueError("only the project owner can issue invitations")
    manifest = membership.get("manifest") or {}
    if manifest.get("project_id") != project.get("project_id") or manifest.get("owner_id") != identity.agent_id:
        raise ValueError("project invitation membership scope mismatch")
    now = int(time.time())
    invitation: Dict[str, Any] = {
        "schema": INVITATION_SCHEMA,
        "invitation_id": str(uuid.uuid4()),
        "project_id": project["project_id"],
        "project_name": project["name"],
        "owner_agent_id": identity.agent_id,
        "invitee_agent_id": invitee.agent_id,
        "invitation_kind": invitation_kind,
        "membership_epoch": int(manifest.get("epoch") or 0),
        "participant_set_hash": participant_set_hash(manifest),
        "membership": membership,
        "issued_at": now,
        "expires_at": now + max(60, int(ttl_seconds)),
        "nonce": str(uuid.uuid4()),
    }
    invitation["signature"] = identity.sign(canonical(_signed_fields(invitation, _INVITATION_SIGNED_FIELDS)))
    return invitation


def verify_project_invitation(
    invitation: Dict[str, Any],
    owner: PublicIdentity,
    invitee: PublicIdentity,
) -> None:
    required = {
        "schema",
        "invitation_id",
        "project_id",
        "project_name",
        "owner_agent_id",
        "invitee_agent_id",
        "invitation_kind",
        "membership_epoch",
        "participant_set_hash",
        "membership",
        "issued_at",
        "expires_at",
        "nonce",
        "signature",
    }
    if not isinstance(invitation, dict) or not required.issubset(invitation):
        raise ValueError("malformed project invitation")
    if invitation.get("schema") != INVITATION_SCHEMA:
        raise ValueError("unsupported project invitation schema")
    if invitation.get("owner_agent_id") != owner.agent_id:
        raise ValueError("project invitation owner mismatch")
    if invitation.get("invitee_agent_id") != invitee.agent_id:
        raise ValueError("project invitation target mismatch")
    now = int(time.time())
    if int(invitation.get("issued_at") or 0) > now + 300:
        raise ValueError("project invitation timestamp is in the future")
    if int(invitation.get("expires_at") or 0) < now:
        raise ValueError("project invitation expired")
    membership = invitation.get("membership") or {}
    manifest = membership.get("manifest") or {}
    manifest_signature = str(membership.get("signature") or "")
    if manifest.get("project_id") != invitation.get("project_id") or manifest.get("owner_id") != owner.agent_id:
        raise ValueError("project invitation manifest scope mismatch")
    if int(manifest.get("epoch") or 0) != int(invitation.get("membership_epoch") or 0):
        raise ValueError("project invitation membership epoch mismatch")
    if participant_set_hash(manifest) != invitation.get("participant_set_hash"):
        raise ValueError("project invitation participant set hash mismatch")
    if not verify_signature(owner.signing_key, manifest_signature, canonical(manifest)):
        raise ValueError("invalid project invitation membership signature")
    history = membership.get("history")
    if history is not None:
        if not isinstance(history, list) or len(history) > 5000:
            raise ValueError("malformed project invitation membership history")
        seen_epochs = set()
        for entry in history:
            historical_manifest = (entry or {}).get("manifest") or {}
            epoch = int(historical_manifest.get("epoch") or 0)
            if epoch <= 0 or epoch in seen_epochs:
                raise ValueError("invalid project invitation membership history epoch")
            seen_epochs.add(epoch)
            if historical_manifest.get("project_id") != invitation.get("project_id") or historical_manifest.get("owner_id") != owner.agent_id:
                raise ValueError("project invitation membership history scope mismatch")
            if participant_set_hash(historical_manifest) != historical_manifest.get("participant_set_hash"):
                raise ValueError("project invitation membership history participant hash mismatch")
            if not verify_signature(owner.signing_key, str((entry or {}).get("signature") or ""), canonical(historical_manifest)):
                raise ValueError("invalid project invitation membership history signature")
    if not verify_signature(owner.signing_key, str(invitation.get("signature") or ""), canonical(_signed_fields(invitation, _INVITATION_SIGNED_FIELDS))):
        raise ValueError("invalid project invitation signature")


def create_invitation_response(
    identity: Identity,
    invitation: Dict[str, Any],
    decision: str,
) -> Dict[str, Any]:
    normalized = str(decision or "").lower()
    if normalized not in INVITATION_DECISIONS:
        raise ValueError("project invitation response must be accept or reject")
    if invitation.get("invitee_agent_id") != identity.agent_id:
        raise ValueError("only the invited node can respond")
    response: Dict[str, Any] = {
        "schema": INVITATION_RESPONSE_SCHEMA,
        "response_id": str(uuid.uuid4()),
        "invitation_id": invitation["invitation_id"],
        "project_id": invitation["project_id"],
        "owner_agent_id": invitation["owner_agent_id"],
        "invitee_agent_id": identity.agent_id,
        "decision": normalized,
        "issued_at": int(time.time()),
        "nonce": str(uuid.uuid4()),
    }
    response["signature"] = identity.sign(canonical(_signed_fields(response, _RESPONSE_SIGNED_FIELDS)))
    return response


def verify_invitation_response(
    response: Dict[str, Any],
    invitation: Dict[str, Any],
    invitee: PublicIdentity,
    owner: PublicIdentity,
) -> None:
    required = {
        "schema",
        "response_id",
        "invitation_id",
        "project_id",
        "owner_agent_id",
        "invitee_agent_id",
        "decision",
        "issued_at",
        "nonce",
        "signature",
    }
    if not isinstance(response, dict) or not required.issubset(response):
        raise ValueError("malformed project invitation response")
    if response.get("schema") != INVITATION_RESPONSE_SCHEMA:
        raise ValueError("unsupported project invitation response schema")
    for key in ("invitation_id", "project_id", "owner_agent_id", "invitee_agent_id"):
        if response.get(key) != invitation.get(key):
            raise ValueError("project invitation response scope mismatch")
    if response.get("owner_agent_id") != owner.agent_id or response.get("invitee_agent_id") != invitee.agent_id:
        raise ValueError("project invitation response identity mismatch")
    if response.get("decision") not in INVITATION_DECISIONS:
        raise ValueError("unsupported project invitation response decision")
    if int(response.get("issued_at") or 0) > int(time.time()) + 300:
        raise ValueError("project invitation response timestamp is in the future")
    if not verify_signature(invitee.signing_key, str(response.get("signature") or ""), canonical(_signed_fields(response, _RESPONSE_SIGNED_FIELDS))):
        raise ValueError("invalid project invitation response signature")
