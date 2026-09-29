"""Run coordinator and workers as separate OS processes over the Relay."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_sidecar.service import Sidecar


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    value = sock.getsockname()[1]
    sock.close()
    return value


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sidecar-process-fl-") as temporary:
        root = Path(temporary)
        relay_port = free_port()
        relay_url = f"http://127.0.0.1:{relay_port}"
        relay = subprocess.Popen([sys.executable, "-m", "agent_sidecar", "relay", "--host", "127.0.0.1", "--port", str(relay_port), "--database", str(root / "relay.db")], cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        workers = []
        coordinator = None
        try:
            deadline = time.time() + 5
            import urllib.request
            while time.time() < deadline:
                try:
                    urllib.request.urlopen(relay_url + "/healthz", timeout=0.2)
                    break
                except Exception:
                    time.sleep(0.05)
            coordinator = Sidecar(root / "coordinator", relay_url)
            nodes = [Sidecar(root / f"node-{i}", relay_url) for i in range(3)]
            for node in nodes:
                coordinator.trust(node.identity.agent_id, node.identity.public().signing_key)
                node.trust(coordinator.identity.agent_id, coordinator.identity.public().signing_key)
            config = root / "coordinator.json"
            config.write_text(json.dumps({"participants": [node.identity.agent_id for node in nodes], "rounds": 3, "strategy": "fedavg", "local_steps": 4, "learning_rate": 0.2}), "utf-8")
            for index, node in enumerate(nodes):
                workers.append(subprocess.Popen([sys.executable, "-m", "agent_sidecar", "federated", "node", "--data-dir", str(root / f"node-{index}"), "--relay", relay_url, "--coordinator-id", coordinator.identity.agent_id, "--duration", "20"], cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
            time.sleep(0.3)
            result = subprocess.run([sys.executable, "-m", "agent_sidecar", "federated", "coordinator", "--data-dir", str(root / "coordinator"), "--relay", relay_url, "--config", str(config), "--timeout", "10"], cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise SystemExit(result.stdout + "\n" + result.stderr)
            print(result.stdout)
        finally:
            for worker in workers:
                worker.terminate()
                worker.wait(timeout=5)
            relay.terminate()
            relay.wait(timeout=5)
