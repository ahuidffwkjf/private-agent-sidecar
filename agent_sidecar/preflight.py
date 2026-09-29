from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .harness_factory import load_harness
from .harness import diagnose_dsh


def run_preflight(data_dir: Optional[Path] = None, harness_config: Optional[Path] = None, relay_url: Optional[str] = None, tls_cert: Optional[Path] = None, tls_key: Optional[Path] = None) -> Dict[str, Any]:
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
    if tls_cert or tls_key:
        checks["tls_cert_present"] = bool(tls_cert and tls_cert.is_file())
        checks["tls_key_present"] = bool(tls_key and tls_key.is_file())
        checks["tls_pair_valid"] = checks["tls_cert_present"] and checks["tls_key_present"]
    if harness_config:
        checks["harness_config"] = str(harness_config)
        try:
            adapter = load_harness(harness_config)
            checks["harness"] = adapter.describe().name
            checks["harness_descriptor"] = True
            if adapter.describe().runtime == "dsh":
                checks["dsh"] = diagnose_dsh(getattr(adapter, "dsh_bin", "dsh"), getattr(adapter, "profile", "headless"))
        except Exception as exc:
            checks["harness_descriptor"] = False
            checks["harness_error"] = str(exc)
    required = [checks.get("cryptography_installed", False)]
    if data_dir is not None:
        required += [checks.get("data_dir_writable", False)]
    if relay_url:
        required += [checks.get("relay_url_valid", False)]
    if tls_cert or tls_key:
        required += [checks.get("tls_pair_valid", False)]
    if harness_config:
        required += [checks.get("harness_descriptor", False)]
    checks["status"] = "ready" if all(required) else "needs_configuration"
    return checks
