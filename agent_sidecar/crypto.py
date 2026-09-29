from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def raw_public(key: Any) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def raw_private(key: Any) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def agent_id_from_signing_key(public_key: bytes) -> str:
    return "agent:" + hashlib.sha256(public_key).hexdigest()[:32]


@dataclass(frozen=True)
class PublicIdentity:
    agent_id: str
    signing_key: str
    encryption_key: str
    proof: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "signing_key": self.signing_key,
            "encryption_key": self.encryption_key,
            "proof": self.proof,
        }

    def verify(self) -> None:
        signing_raw = b64d(self.signing_key)
        if agent_id_from_signing_key(signing_raw) != self.agent_id:
            raise ValueError("agent_id does not match signing key")
        payload = canonical(
            {
                "agent_id": self.agent_id,
                "signing_key": self.signing_key,
                "encryption_key": self.encryption_key,
            }
        )
        Ed25519PublicKey.from_public_bytes(signing_raw).verify(b64d(self.proof), payload)

    @classmethod
    def from_dict(cls, value: Dict[str, str]) -> "PublicIdentity":
        identity = cls(
            agent_id=value["agent_id"],
            signing_key=value["signing_key"],
            encryption_key=value["encryption_key"],
            proof=value["proof"],
        )
        identity.verify()
        return identity


class Identity:
    def __init__(self, signing_private: Ed25519PrivateKey, encryption_private: X25519PrivateKey):
        self.signing_private = signing_private
        self.encryption_private = encryption_private

    @property
    def agent_id(self) -> str:
        return agent_id_from_signing_key(raw_public(self.signing_private.public_key()))

    def public(self) -> PublicIdentity:
        body = {
            "agent_id": self.agent_id,
            "signing_key": b64e(raw_public(self.signing_private.public_key())),
            "encryption_key": b64e(raw_public(self.encryption_private.public_key())),
        }
        return PublicIdentity(**body, proof=b64e(self.signing_private.sign(canonical(body))))

    def sign(self, value: bytes) -> str:
        return b64e(self.signing_private.sign(value))

    def derive_key(self, peer_public_key: str, salt: bytes, info: bytes) -> bytes:
        peer = X25519PublicKey.from_public_bytes(b64d(peer_public_key))
        shared = self.encryption_private.exchange(peer)
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(shared)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "signing_private": b64e(raw_private(self.signing_private)),
            "encryption_private": b64e(raw_private(self.encryption_private)),
        }
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, canonical(value))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)

    @classmethod
    def generate(cls) -> "Identity":
        return cls(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: Path) -> "Identity":
        if not path.exists():
            identity = cls.generate()
            identity.save(path)
            return identity
        value = json.loads(path.read_text("utf-8"))
        return cls(
            Ed25519PrivateKey.from_private_bytes(b64d(value["signing_private"])),
            X25519PrivateKey.from_private_bytes(b64d(value["encryption_private"])),
        )


def verify_signature(public_key: str, signature: str, value: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(b64d(public_key)).verify(b64d(signature), value)
        return True
    except (InvalidSignature, ValueError):
        return False

