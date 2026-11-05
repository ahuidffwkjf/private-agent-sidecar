from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse



def run_preflight(data_dir: Optional[Path] = None, harness_config: Optional[Path] = None, relay_url: Optional[str] = None, tls_cert: Optional[Path] = None, tls_key: Optional[Path] = None, project_key_backend: str = "sqlite", project_key_command: Optional[list[str]] = None, direct_endpoint: Optional[str] = None, production: bool = False) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    checks["cryptography_installed"] = importlib.util.find_spec("cryptography") is not None
    if data_dir is not None:
        checks["data_dir"] = str(data_dir)
        checks["data_dir_exists"] = data_dir.exists()
        checks["data_dir_writable"] = data_dir.exists() and data_dir.is_dir() and __import__("os").access(data_dir, __import__("os").W_OK)
    if relay_url:
        parsed = urlparse(relay_url)
        checks["relay_url"] = relay_url
        checks["relay_url_valid"] = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        checks["relay_url_https"] = parsed.scheme == "https"
    if direct_endpoint:
        parsed_direct = urlparse(direct_endpoint)
        checks["direct_endpoint"] = direct_endpoint
        checks["direct_endpoint_valid"] = parsed_direct.scheme in {"http", "https"} and bool(parsed_direct.netloc)
        checks["direct_endpoint_https"] = parsed_direct.scheme == "https"
    if tls_cert or tls_key:
        checks["tls_cert_present"] = bool(tls_cert and tls_cert.is_file())
        checks["tls_key_present"] = bool(tls_key and tls_key.is_file())
        checks["tls_pair_valid"] = checks["tls_cert_present"] and checks["tls_key_present"]
    backend = str(project_key_backend or "sqlite").lower()
    checks["project_key_backend"] = backend
    checks["project_key_backend_valid"] = backend in {"sqlite", "file", "keychain", "external"}
    if backend == "keychain":
        checks["project_key_backend_available"] = shutil.which("security") is not None
    elif backend == "external":
        command = list(project_key_command or [])
        checks["project_key_command"] = command[0] if command else ""
        checks["project_key_command_valid"] = bool(command)
        checks["project_key_backend_available"] = bool(command and shutil.which(command[0]))
    else:
        checks["project_key_backend_available"] = True
    if harness_config:
        checks["harness_config"] = str(harness_config)
        try:
            from .harness_factory import load_harness
            from .harness import diagnose_dsh
            adapter = load_harness(harness_config)
            checks["harness"] = adapter.describe().name
            checks["harness_descriptor"] = True
            if adapter.describe().runtime == "dsh":
                checks["dsh"] = diagnose_dsh(getattr(adapter, "dsh_bin", "dsh"), getattr(adapter, "profile", "headless"))
        except Exception as exc:
            checks["harness_descriptor"] = False
            checks["harness_error"] = str(exc)
    required = [checks.get("cryptography_installed", False), checks.get("project_key_backend_valid", False), checks.get("project_key_backend_available", False)]
    if data_dir is not None:
        required += [checks.get("data_dir_writable", False)]
    if relay_url:
        required += [checks.get("relay_url_valid", False)]
    if direct_endpoint:
        required += [checks.get("direct_endpoint_valid", False)]
    if tls_cert or tls_key:
        required += [checks.get("tls_pair_valid", False)]
    if harness_config:
        required += [checks.get("harness_descriptor", False)]
    if production:
        checks["production_mode"] = True
        required += [checks.get("relay_url_https", False), checks.get("direct_endpoint_https", False), checks.get("tls_pair_valid", False), backend != "sqlite"]
        checks["production_key_backend_non_sqlite"] = backend != "sqlite"
    checks["status"] = "ready" if all(required) else "needs_configuration"
    return checks
