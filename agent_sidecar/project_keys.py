from __future__ import annotations

import hashlib
import os
from typing import Any, Dict

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from .crypto import b64d, b64e, canonical

PROJECT_KEY_PROTOCOL = "project-content-key/1"


def generate_project_key() -> bytes:
    return os.urandom(32)


def encrypt_content(key: bytes, content: Any) -> Dict[str, str]:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("project content key must be 32 bytes")
    nonce = os.urandom(12)
    plaintext = canonical(content)
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, None)
    return {"protocol": PROJECT_KEY_PROTOCOL, "nonce": b64e(nonce), "ciphertext": b64e(ciphertext)}


def decrypt_content(key: bytes, encrypted: Dict[str, Any]) -> Any:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("project content key must be 32 bytes")
    if encrypted.get("protocol") != PROJECT_KEY_PROTOCOL:
        raise ValueError("unsupported project content encryption protocol")
    plaintext = ChaCha20Poly1305(key).decrypt(b64d(str(encrypted["nonce"])), b64d(str(encrypted["ciphertext"])), None)
    import json
    return json.loads(plaintext.decode("utf-8"))


def key_fingerprint(key: bytes) -> str:
    return "sha256:" + hashlib.sha256(key).hexdigest()
