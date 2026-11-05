"""Verify Relay outage and a two-sided project-board partition using OS processes."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
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


def wait_until(fn, timeout: float = 12.0):
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


def stop(process: subprocess.Popen | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sidecar-partition-") as temporary:
        root = Path(temporary)
        relay_port = free_port()
        relay_url = f"http://127.0.0.1:{relay_port}"
        ports = {name: free_port() for name in ("owner", "member-b", "member-c")}
        urls = {name: f"http://127.0.0.1:{port}" for name, port in ports.items()}
        processes: dict[str, subprocess.Popen] = {}
        relay: subprocess.Popen | None = None

        def start_relay() -> subprocess.Popen:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_sidecar", "relay", "--host", "127.0.0.1", "--port", str(relay_port), "--database", str(root / "relay.db")],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            wait_health(relay_url)
            return process

        def start_node(name: str) -> subprocess.Popen:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_sidecar", "serve", "--host", "127.0.0.1", "--port", str(ports[name]), "--data-dir", str(root / name), "--relay", relay_url, "--direct-endpoint", urls[name]],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            wait_health(urls[name])
            processes[name] = process
            return process

        try:
            relay = start_relay()
            for name in urls:
                start_node(name)
            identities = {name: request(url + "/v1/identity") for name, url in urls.items()}
            project_id = "partition-board"
            request(urls["owner"] + "/v1/projects", "POST", {"name": "Partition Board", "project_id": project_id})
            for invited in ("member-b", "member-c"):
                request(urls["owner"] + f"/v1/projects/{project_id}/invitations", "POST", {"agent_id": identities[invited]["agent_id"]})
                invitation = wait_until(lambda invited=invited: request(urls[invited] + "/v1/project-invitations").get("incoming"))[-1]
                request(urls[invited] + f"/v1/project-invitations/{invitation['invitation_id']}/response", "POST", {"decision": "accept"})
                wait_until(lambda: any(value.get("status") == "completed" for value in request(urls["owner"] + "/v1/project-invitations").get("outgoing", [])))
            for name in urls:
                wait_until(lambda name=name: len(request(urls[name] + f"/v1/projects/{project_id}").get("project_members", [])) >= 3)
                request(urls[name] + f"/v1/projects/{project_id}/gossip-settings", "POST", {"gossip_fanout": 1})

            # Relay outage: a member can still commit a local board event.
            # Direct board synchronization is retried after Relay recovery; the
            # board itself never depends on Relay for local writes.
            stop(relay)
            relay = None
            relay_event = request(urls["member-b"] + f"/v1/projects/{project_id}/board", "POST", {"event_type": "post", "content": {"text": "relay offline"}})["event"]
            local_board = request(urls["member-b"] + f"/v1/projects/{project_id}/board")
            assert relay_event["event_id"] in {event["event_id"] for event in local_board["events"]}
            relay = start_relay()

            # Partition 1: only B remains online and creates an event.
            stop(processes["owner"]); stop(processes["member-c"])
            b_event = request(urls["member-b"] + f"/v1/projects/{project_id}/board", "POST", {"event_type": "post", "content": {"text": "partition B"}})["event"]
            stop(processes["member-b"])

            # Partition 2: only C is online and independently creates an event.
            start_node("member-c")
            c_event = request(urls["member-c"] + f"/v1/projects/{project_id}/board", "POST", {"event_type": "post", "content": {"text": "partition C"}})["event"]

            # Heal partition and repeatedly run fanout=1 Merkle Gossip.
            start_node("member-b"); start_node("owner")
            target_ids = {relay_event["event_id"], b_event["event_id"], c_event["event_id"]}
            final_boards = None
            for _ in range(8):
                for name in ("owner", "member-b", "member-c"):
                    request(urls[name] + f"/v1/projects/{project_id}/board/sync/merkle", "POST", {})
                boards = {name: request(url + f"/v1/projects/{project_id}/board") for name, url in urls.items()}
                if all(target_ids.issubset({event["event_id"] for event in board["events"]}) for board in boards.values()) and len({board["event_set_root"] for board in boards.values()}) == 1:
                    final_boards = boards
                    break
            assert final_boards is not None
            # Continue several stable cycles after convergence. A healthy
            # implementation should neither create duplicates nor diverge
            # again after the propagation TTL expires.
            stable_roots = []
            for _ in range(3):
                for name in ("owner", "member-b", "member-c"):
                    request(urls[name] + f"/v1/projects/{project_id}/board/sync/merkle", "POST", {})
                stable = {name: request(url + f"/v1/projects/{project_id}/board") for name, url in urls.items()}
                stable_roots.append({name: board["event_set_root"] for name, board in stable.items()})
                for board in stable.values():
                    ids = [event["event_id"] for event in board["events"]]
                    assert len(ids) == len(set(ids))
            assert len({json.dumps(value, sort_keys=True) for value in stable_roots}) == 1
            print(json.dumps({
                "project_id": project_id,
                "relay_outage_local_commit": True,
                "partition_event_ids": sorted(target_ids),
                "converged_root": next(iter({board["event_set_root"] for board in final_boards.values()})),
                "event_counts": {name: board["event_count"] for name, board in final_boards.items()},
                "gossip_fanout": 1,
                "stable_cycles": len(stable_roots),
                "duplicate_free_after_convergence": True,
            }, ensure_ascii=False, indent=2))
        finally:
            for process in processes.values():
                stop(process)
            stop(relay)
