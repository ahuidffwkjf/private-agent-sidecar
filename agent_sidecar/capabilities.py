"""Node capability discovery and Relay connectivity measurements."""
from __future__ import annotations

import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

CAPABILITY_SCHEMA = "sidecar-node-capabilities/1"


def _run_json(command: list[str], timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        value = json.loads(completed.stdout)
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _memory_bytes() -> Optional[int]:
    if sys.platform == "darwin":
        try:
            value = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=1, check=False)
            return int(value.stdout.strip()) if value.returncode == 0 else None
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text("utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                try:
                    return int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    return None
    return None


def _gpu_info() -> Dict[str, Any]:
    names: list[str] = []
    source = "none"
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                source = "nvidia-smi"
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not names and sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        names = ["Apple Silicon integrated GPU"]
        source = "platform"
    if not names and sys.platform == "darwin" and shutil.which("system_profiler"):
        value = _run_json(["system_profiler", "SPDisplaysDataType", "-json"], timeout=3)
        if value:
            for item in value.get("SPDisplaysDataType", []):
                name = item.get("sppci_model") or item.get("_name")
                if name:
                    names.append(str(name))
            if names:
                source = "system_profiler"
    return {"present": bool(names), "count": len(names), "names": names, "source": source}


def _camera_info() -> Dict[str, Any]:
    names: list[str] = []
    source = "none"
    if sys.platform == "darwin" and shutil.which("system_profiler"):
        value = _run_json(["system_profiler", "SPCameraDataType", "-json"], timeout=3)
        if value:
            for item in value.get("SPCameraDataType", []):
                name = item.get("_name") or item.get("spcamera_model-id")
                if name:
                    names.append(str(name))
            source = "system_profiler"
    elif sys.platform.startswith("linux"):
        names = [path.name for path in sorted(Path("/dev").glob("video*"))]
        source = "/dev/video*"
    return {"present": bool(names), "count": len(names), "names": names, "source": source}


@lru_cache(maxsize=1)
def detect_hardware() -> Dict[str, Any]:
    return {
        "platform": platform.system().lower(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "cpu_logical_cores": os.cpu_count(),
        "memory_bytes": _memory_bytes(),
        "gpu": _gpu_info(),
        "camera": _camera_info(),
    }


def probe_relay(relay_url: str, access_token: str = "", attempts: int = 3, timeout: float = 1.0) -> Dict[str, Any]:
    latencies: list[float] = []
    failures: list[str] = []
    attempts = min(max(int(attempts), 1), 10)
    headers = {"Accept": "application/json"}
    if access_token:
        headers["X-Relay-Token"] = access_token
    for _ in range(attempts):
        started = time.perf_counter()
        try:
            with urlopen(Request(relay_url.rstrip("/") + "/healthz", headers=headers), timeout=timeout) as response:
                response.read()
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
            latencies.append((time.perf_counter() - started) * 1000.0)
        except Exception as exc:
            failures.append(type(exc).__name__)
    success_count = len(latencies)
    success_rate = success_count / attempts
    average = statistics.fmean(latencies) if latencies else None
    p95 = sorted(latencies)[max(0, min(len(latencies) - 1, int(len(latencies) * 0.95)))] if latencies else None
    jitter = statistics.pstdev(latencies) if len(latencies) > 1 else (0.0 if latencies else None)
    if success_rate == 1.0 and (p95 or 0) < 500 and (jitter or 0) < 100:
        stability = "stable"
    elif success_rate > 0:
        stability = "degraded"
    else:
        stability = "unreachable"
    return {
        "relay_reachable": success_count > 0,
        "probe_attempts": attempts,
        "probe_successes": success_count,
        "success_rate": success_rate,
        "average_latency_ms": round(average, 3) if average is not None else None,
        "p95_latency_ms": round(p95, 3) if p95 is not None else None,
        "jitter_ms": round(jitter, 3) if jitter is not None else None,
        "stability": stability,
        "failure_types": sorted(set(failures)),
        "connection_type": "unknown",
    }


def load_custom_capabilities(data_dir: Path) -> Dict[str, Any]:
    path = data_dir / "capabilities.override.json"
    if not path.exists():
        path.write_text("{}\n", "utf-8")
        return {}
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def collect_capabilities(agent_id: str, data_dir: Path, relay_url: str, relay_token: str = "") -> Dict[str, Any]:
    return {
        "schema": CAPABILITY_SCHEMA,
        "session_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "announced_at": int(time.time()),
        "hardware": detect_hardware(),
        "network": probe_relay(relay_url, relay_token),
        "runtime": {
            "python": platform.python_version(),
            "sidecar_protocols": ["agent-message/1", "agent-broadcast/1", "sidecar-mcp/1"],
        },
        "services": {
            "public_broadcast": True,
            "encrypted_messages": True,
            "mcp": True,
            "trace_storage": True,
            "user_skills": True,
            "federated_learning": True,
        },
        "custom": load_custom_capabilities(data_dir),
    }
