from __future__ import annotations

import hashlib
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence


class ProjectKeyBackend:
    name = "abstract"

    def save(self, project_id: str, epoch: int, key_b64: str, key_fingerprint: str) -> None:
        raise NotImplementedError

    def get(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def delete(self, project_id: str, epoch: int) -> None:
        raise NotImplementedError


class SQLiteProjectKeyBackend(ProjectKeyBackend):
    name = "sqlite"

    def save(self, project_id: str, epoch: int, key_b64: str, key_fingerprint: str) -> None:
        return None

    def get(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        return None

    def delete(self, project_id: str, epoch: int) -> None:
        return None


class FileProjectKeyBackend(ProjectKeyBackend):
    """0600 local fallback; use Keychain/TPM/HSM for production deployments."""

    name = "file"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            os.chmod(self.path, 0o600)

    @staticmethod
    def _slot(project_id: str, epoch: int) -> str:
        return hashlib.sha256(f"{project_id}:{int(epoch)}".encode()).hexdigest()

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("project key file must contain an object")
        return value

    def _write(self, value: Dict[str, Any]) -> None:
        fd, name = tempfile.mkstemp(prefix=".project-keys-", dir=self.path.parent)
        temporary = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def save(self, project_id: str, epoch: int, key_b64: str, key_fingerprint: str) -> None:
        values = self._read()
        slot = self._slot(project_id, epoch)
        existing = values.get(slot)
        record = {"project_id": project_id, "epoch": int(epoch), "key_b64": key_b64, "key_fingerprint": key_fingerprint}
        if existing and existing != record:
            raise ValueError("external project key was reused with different material")
        values[slot] = record
        self._write(values)

    def get(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        value = self._read().get(self._slot(project_id, epoch))
        return dict(value) if isinstance(value, dict) else None

    def delete(self, project_id: str, epoch: int) -> None:
        values = self._read()
        if values.pop(self._slot(project_id, epoch), None) is not None:
            self._write(values)


class KeychainProjectKeyBackend(ProjectKeyBackend):
    """macOS Keychain backend using `security`; never prints key material."""

    name = "keychain"
    service = "private-agent-sidecar/project-content-key/v1"

    def __init__(self, command: str = "security", runner: Optional[Callable[..., Any]] = None):
        self.command = command
        self.runner = runner or subprocess.run
        if runner is None and shutil.which(command) is None:
            raise RuntimeError("macOS security command is unavailable; use --project-key-backend file or sqlite")

    @staticmethod
    def _account(project_id: str, epoch: int) -> str:
        return hashlib.sha256(f"{project_id}:{int(epoch)}".encode()).hexdigest()

    def _run(self, args: list[str], check: bool = True):
        return self.runner([self.command, *args], check=check, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def save(self, project_id: str, epoch: int, key_b64: str, key_fingerprint: str) -> None:
        # The fingerprint is stored in the SQLite metadata table; only the key
        # material goes into Keychain.
        self._run(["add-generic-password", "-U", "-a", self._account(project_id, epoch), "-s", self.service, "-w", key_b64])

    def get(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        result = self._run(["find-generic-password", "-a", self._account(project_id, epoch), "-s", self.service, "-w"], check=False)
        if getattr(result, "returncode", 1) != 0:
            return None
        key_b64 = str(getattr(result, "stdout", "") or "").strip()
        if not key_b64:
            return None
        try:
            raw_key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
        except Exception as exc:
            raise RuntimeError("macOS Keychain returned invalid project key material") from exc
        if len(raw_key) != 32:
            raise RuntimeError("macOS Keychain returned an invalid project key length")
        return {
            "project_id": project_id,
            "epoch": int(epoch),
            "key_b64": key_b64,
            "key_fingerprint": "sha256:" + hashlib.sha256(raw_key).hexdigest(),
        }

    def delete(self, project_id: str, epoch: int) -> None:
        self._run(["delete-generic-password", "-a", self._account(project_id, epoch), "-s", self.service], check=False)


class ExternalCommandProjectKeyBackend(ProjectKeyBackend):
    """Adapter for an operator-managed TPM/HSM helper process.

    The helper receives one JSON request on stdin and must return one JSON
    response on stdout. Secret key material is never placed in argv or logs.
    Supported operations are ``save``, ``get`` and ``delete``. A helper can
    wrap tpm2-tools, a PKCS#11 utility, a cloud KMS client, or an enterprise
    HSM daemon without coupling Sidecar to vendor APIs.
    """

    name = "external"

    def __init__(self, command: Sequence[str], runner: Optional[Callable[..., Any]] = None):
        if not command:
            raise ValueError("external project-key backend requires a command")
        self.command = list(command)
        self.runner = runner or subprocess.run

    def _run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result = self.runner(
            self.command,
            input=encoded,
            check=False,
            capture_output=True,
            text=True,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError("external project-key helper failed")
        try:
            response = json.loads(str(getattr(result, "stdout", "") or ""))
        except json.JSONDecodeError as exc:
            raise RuntimeError("external project-key helper returned invalid JSON") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError("external project-key helper rejected the operation")
        return response

    def save(self, project_id: str, epoch: int, key_b64: str, key_fingerprint: str) -> None:
        self._run({"operation": "save", "project_id": project_id, "epoch": int(epoch), "key_b64": key_b64, "key_fingerprint": key_fingerprint})

    def get(self, project_id: str, epoch: int) -> Optional[Dict[str, Any]]:
        response = self._run({"operation": "get", "project_id": project_id, "epoch": int(epoch)})
        record = response.get("record")
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("project_id") != project_id or int(record.get("epoch", -1)) != int(epoch):
            raise RuntimeError("external project-key helper returned a scope-mismatched record")
        if not record.get("key_b64") or not record.get("key_fingerprint"):
            raise RuntimeError("external project-key helper returned an incomplete record")
        return dict(record)

    def delete(self, project_id: str, epoch: int) -> None:
        self._run({"operation": "delete", "project_id": project_id, "epoch": int(epoch)})


def create_project_key_backend(kind: str, data_dir: Path, command: Optional[Sequence[str]] = None) -> ProjectKeyBackend:
    normalized = str(kind or "sqlite").lower()
    if normalized == "sqlite":
        return SQLiteProjectKeyBackend()
    if normalized == "file":
        return FileProjectKeyBackend(data_dir / "project-keys.json")
    if normalized == "keychain":
        return KeychainProjectKeyBackend()
    if normalized in {"external", "command", "hsm", "tpm"}:
        return ExternalCommandProjectKeyBackend(command or [])
    raise ValueError("project key backend must be sqlite, file, keychain or external")
