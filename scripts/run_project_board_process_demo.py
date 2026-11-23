"""Exercise project invitations, Owner outage, encrypted board sync and recovery in OS processes."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    value = sock.getsockname()[1]
    sock.close()
    return value


def request(url: str, method: str = "GET", body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    with urlopen(req, timeout=4) as response:
        return json.loads(response.read().decode())


def wait_health(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if request(url + "/healthz").get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"timeout waiting for {url}")


def wait_until(fn, timeout: float = 10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"condition timeout: {last!r}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sidecar-project-process-") as temporary:
        root = Path(temporary)
        relay_port = free_port()
        relay_url = f"http://127.0.0.1:{relay_port}"
        relay = subprocess.Popen(
            [sys.executable, "-m", "agent_sidecar", "relay", "--host", "127.0.0.1", "--port", str(relay_port), "--database", str(root / "relay.db")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        nodes = {}
        processes = {}
        ports = {name: free_port() for name in ("owner", "member-b", "member-c")}
        try:
            wait_health(relay_url)
            for name, port in ports.items():
                data_dir = root / name
                endpoint = f"http://127.0.0.1:{port}"
                process = subprocess.Popen(
                    [sys.executable, "-m", "agent_sidecar", "serve", "--host", "127.0.0.1", "--port", str(port), "--data-dir", str(data_dir), "--relay", relay_url, "--direct-endpoint", endpoint],
                    cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processes[name] = process
                wait_health(endpoint)
                nodes[name] = endpoint

            identities = {name: request(url + "/v1/identity") for name, url in nodes.items()}
            project_id = "process-board"
            request(nodes["owner"] + "/v1/projects", "POST", {"name": "Process Board", "project_id": project_id})
            for invited in ("member-b", "member-c"):
                request(nodes["owner"] + f"/v1/projects/{project_id}/invitations", "POST", {"agent_id": identities[invited]["agent_id"]})
                invitation = wait_until(lambda invited=invited: request(nodes[invited] + "/v1/project-invitations").get("incoming"))[-1]
                request(nodes[invited] + f"/v1/project-invitations/{invitation['invitation_id']}/response", "POST", {"decision": "accept"})
                wait_until(lambda: request(nodes["owner"] + "/v1/project-invitations").get("outgoing", [{}])[0].get("status") == "completed")

            wait_until(lambda: len(request(nodes["member-b"] + f"/v1/projects/{project_id}").get("project_members", [])) >= 3)
            wait_until(lambda: len(request(nodes["member-c"] + f"/v1/projects/{project_id}").get("project_members", [])) >= 3)
            for name in ("owner", "member-b", "member-c"):
                request(nodes[name] + f"/v1/projects/{project_id}/gossip-settings", "POST", {"gossip_fanout": 1})

            # Owner outage: the two members continue to create and force-sync a board event.
            processes["owner"].terminate()
            processes["owner"].wait(timeout=5)
            offline_event = request(nodes["member-b"] + f"/v1/projects/{project_id}/board", "POST", {"event_type": "post", "content": {"text": "owner is offline"}})
            request(nodes["member-b"] + f"/v1/projects/{project_id}/board/sync", "POST", {})
            member_board = wait_until(lambda: request(nodes["member-c"] + f"/v1/projects/{project_id}/board").get("events"))
            assert any((event.get("content") or {}).get("text") == "owner is offline" for event in member_board)

            # Restart Owner with the same identity/database, then Merkle-reconcile its replica.
            owner_port = ports["owner"]
            processes["owner"] = subprocess.Popen(
                [sys.executable, "-m", "agent_sidecar", "serve", "--host", "127.0.0.1", "--port", str(owner_port), "--data-dir", str(root / "owner"), "--relay", relay_url, "--direct-endpoint", nodes["owner"]],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            wait_health(nodes["owner"])
            recovered = wait_until(lambda: request(nodes["owner"] + f"/v1/projects/{project_id}/board/sync/merkle", "POST", {}).get("event_count", 0) >= 1)
            owner_board = request(nodes["owner"] + f"/v1/projects/{project_id}/board")
            assert any((event.get("content") or {}).get("text") == "owner is offline" for event in owner_board.get("events", []))

            # Remove member C, rotate the membership/content key epoch, and
            # verify that C no longer has project-board access.
            request(nodes["owner"] + f"/v1/projects/{project_id}/members/remove", "POST", {"agent_id": identities["member-c"]["agent_id"]})
            request(nodes["owner"] + f"/v1/projects/{project_id}/board", "POST", {"event_type": "post", "content": {"text": "after member removal"}})
            removed_member_rejected = False
            try:
                request(nodes["member-c"] + f"/v1/projects/{project_id}/board")
            except Exception:
                removed_member_rejected = True
            assert removed_member_rejected
            print(json.dumps({"project_id": project_id, "owner_outage_event_id": offline_event["event"]["event_id"], "owner_recovery": recovered, "member_c_event_count": len(member_board), "owner_event_count": owner_board["event_count"], "removed_member_rejected": removed_member_rejected}, ensure_ascii=False, indent=2))
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            if relay.poll() is None:
                relay.terminate()
                relay.wait(timeout=5)
