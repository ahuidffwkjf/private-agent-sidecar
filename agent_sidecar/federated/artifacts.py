from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ArtifactChunk:
    artifact_id: str
    index: int
    total: int
    payload: bytes
    chunk_sha256: str
    artifact_sha256: str


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def chunk_artifact(artifact_id: str, payload: bytes, chunk_size: int = 256 * 1024) -> List[ArtifactChunk]:
    if not artifact_id:
        raise ValueError("artifact_id is required")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    artifact_hash = sha256_bytes(payload)
    parts = [payload[offset:offset + chunk_size] for offset in range(0, len(payload), chunk_size)] or [b""]
    total = len(parts)
    return [ArtifactChunk(artifact_id, index, total, part, sha256_bytes(part), artifact_hash) for index, part in enumerate(parts)]


def assemble_artifact(chunks: Iterable[ArtifactChunk]) -> bytes:
    ordered = sorted(list(chunks), key=lambda chunk: chunk.index)
    if not ordered:
        raise ValueError("at least one chunk is required")
    expected_total = ordered[0].total
    expected_hash = ordered[0].artifact_sha256
    if len(ordered) != expected_total or [chunk.index for chunk in ordered] != list(range(expected_total)):
        raise ValueError("artifact chunks are incomplete or duplicated")
    for chunk in ordered:
        if chunk.total != expected_total or chunk.artifact_sha256 != expected_hash:
            raise ValueError("artifact chunk metadata mismatch")
        if sha256_bytes(chunk.payload) != chunk.chunk_sha256:
            raise ValueError("artifact chunk hash mismatch")
    payload = b"".join(chunk.payload for chunk in ordered)
    if sha256_bytes(payload) != expected_hash:
        raise ValueError("artifact hash mismatch")
    return payload
