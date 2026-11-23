"""Build a portable, secret-free toy source release from an explicit allowlist."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sidecar_setup import ROOT, source_files as installer_files

RELEASE_ID = "toy-v0.3.0-20261123"
PREFIX = "sidecar-toy/"
DOCS = (
    "TOY_USER_GUIDE.md", "TOY_APP_GUIDE.md", "PROJECT_BOARD_SPEC.md", "PROJECT_INVITATION_SPEC.md",
    "NODE_TRUST_SPEC.md", "PROJECT_TRUST_SPEC.md", "BROADCAST_SPEC.md",
    "CHAT_UI.md", "HARNESS_SPEC.md", "COLLABORATION_SPEC.md",
)
SCRIPTS = (
    "package_toy_release.py", "check_chat_ui.mjs", "check_skill_ui.mjs",
    "check_toy_ui.mjs", "check_dsh_toy_plugin.mjs", "legacy_dashboard_proxy.py",
    "run_production_preflight_demo.py", "run_project_board_process_demo.py",
    "run_project_board_partition_demo.py", "run_tailcat_recovery_demo.py",
    "start_chat_preview.py", "verify_agent_install.py", "demo.py",
)
PLUGIN_FILES = (
    "package.json", "README.md", "cordis.patch.yml", "lib/index.js", "lib/types/index.d.ts",
)
ASSETS = {"index.html", "app.js", "toy.js", "app.css", "lucide.js", "LUCIDE-LICENSE"}
PRIVATE_KEY = re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
ACCESS_TOKEN = re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-proj-[A-Za-z0-9_-]{30,})\b")


def allowed_path(name):
    path = Path(name)
    forbidden = {"runtime", "runs", "output", "dist", "build", ".git", ".venv", "node_modules",
                 "__pycache__", "packages", "private-exports", "retired-skills", "backend-dist", "release"}
    if path.is_absolute() or ".." in path.parts or forbidden.intersection(path.parts):
        return False
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".key", ".pem", ".private", ".pyc", ".log", ".env"}:
        return False
    if any(part.endswith((".egg-info", ".private.json")) for part in path.parts):
        return False
    if path.name in {"authorized_keys", "identity.json", "workspace.json", "relay-token", "access.json", "toy-config.json"}:
        return False
    if name.startswith("agent_sidecar/web/") and path.name not in ASSETS:
        return False
    return True


def release_files(root=ROOT):
    root = Path(root).resolve()
    files = set(installer_files(root))
    files.update(root / name for name in (".gitignore", "AGENTS.md", "CONTINUOUS_GOAL.md", "requirements.txt", "RELEASE_NOTES.md"))
    files.update(root / "docs" / name for name in DOCS)
    files.update(root / "scripts" / name for name in SCRIPTS)
    files.update((root / "tests").glob("test_*.py"))
    files.update((root / "docs/interfaces").glob("*.md"))
    files.update((root / "docs/interfaces/examples").glob("*.json"))
    files.update(root / "plugins/dsh-private-agent-sidecar" / name for name in PLUGIN_FILES)
    for path in sorted(files):
        name = path.relative_to(root).as_posix()
        if not allowed_path(name):
            raise ValueError("unsafe distribution path: " + name)
        if not path.is_file() or path.is_symlink() or any((root / parent).is_symlink() for parent in path.relative_to(root).parents):
            raise ValueError("missing file or symlink: " + name)
    return sorted(files)


def source_content(root=ROOT):
    root = Path(root).resolve()
    content = {path.relative_to(root).as_posix(): path.read_bytes() for path in release_files(root)}
    # The release starts with the practical user guide, not historical roadmap text.
    content["README.md"] = content["docs/TOY_USER_GUIDE.md"]
    for name, data in content.items():
        if PRIVATE_KEY.search(data) or ACCESS_TOKEN.search(data):
            raise ValueError("credential-like material detected in: " + name)
    return content


def content_digest(content):
    digest = hashlib.sha256()
    for name, data in sorted(content.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def package(output, root=ROOT):
    output = Path(output)
    checksum = output.with_suffix(output.suffix + ".sha256")
    if output.exists() or checksum.exists():
        raise ValueError("choose a fresh output path; nothing overwritten")
    content = source_content(root)
    source_hash = content_digest(content)
    manifest = {"schema": "sidecar-toy-release/1", "release_id": RELEASE_ID,
                "package_version": "0.3.0", "source_sha256": source_hash,
                "entry": "README.md", "python": ">=3.9", "verified_launcher_platform": "macOS",
                "distribution": "source; dependencies downloaded at install time",
                "summary": "demo extractor unless publisher Harness explicitly configured",
                "includes_credentials": False, "includes_runtime_data": False,
                "includes_federated_training": False,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(content.items())}}
    content["MANIFEST.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    content["SHA256SUMS"] = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(content.items())).encode()
    if content_digest(source_content(root)) != source_hash:
        raise ValueError("source changed while packaging; retry with stable files")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo(PREFIX + name, (2026, 11, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name.endswith((".sh", ".command")) else 0o100644) << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum.write_text(digest + "  " + output.name + "\n")
    return {"file": str(output.resolve()), "sha256": digest, "source_sha256": source_hash,
            "entries": len(content), "bytes": output.stat().st_size, "release_id": RELEASE_ID}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(package(parser.parse_args().output), indent=2))
