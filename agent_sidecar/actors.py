"""Node-attested Harness actor identities; node trust remains the authority."""
import re
import time
import uuid

from .crypto import canonical, verify_signature

SCHEMA = "sidecar-harness-actor/1"


def create_actor(identity, name, role=""):
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("actor name must contain 1 to 100 characters")
    if not isinstance(role, str) or len(role) > 500:
        raise ValueError("actor role must contain at most 500 characters")
    node_key_id = identity.agent_id.removeprefix("agent:")
    profile = {"schema": SCHEMA, "actor_id": f"actor:{node_key_id}:{uuid.uuid4().hex}",
               "node_agent_id": identity.agent_id, "harness_id": f"harness:{node_key_id}",
               "name": name.strip(), "role": role.strip(), "created_at": int(time.time())}
    return {**profile, "signature": identity.sign(canonical(profile))}


def verify_actor(profile, node):
    if not isinstance(profile, dict) or set(profile) != {"schema", "actor_id", "node_agent_id", "harness_id", "name", "role", "created_at", "signature"}:
        raise ValueError("invalid actor identity")
    key_id = node.agent_id.removeprefix("agent:")
    if (profile["schema"] != SCHEMA or profile["node_agent_id"] != node.agent_id
            or profile["harness_id"] != f"harness:{key_id}"
            or not isinstance(profile["actor_id"], str)
            or not re.fullmatch(r"actor:" + re.escape(key_id) + r":[a-f0-9]{32}", profile["actor_id"])):
        raise ValueError("actor does not belong to the signing node")
    if not isinstance(profile["name"], str) or not 1 <= len(profile["name"].strip()) <= 100:
        raise ValueError("invalid actor name")
    if not isinstance(profile["role"], str) or len(profile["role"]) > 500:
        raise ValueError("invalid actor role")
    if not isinstance(profile["signature"], str) or type(profile["created_at"]) is not int or profile["created_at"] < 0:
        raise ValueError("invalid actor signature or timestamp")
    unsigned = {k: v for k, v in profile.items() if k != "signature"}
    if not verify_signature(node.signing_key, profile["signature"], canonical(unsigned)):
        raise ValueError("invalid actor signature")
    return profile
