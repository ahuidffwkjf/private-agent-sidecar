"""Exercise a four-node bounty tree and preserve a small protocol benchmark."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_sidecar.crypto import canonical
from agent_sidecar.decisions import FEATURES
from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar
from agent_sidecar.store import RelayStore


def run(output):
    if output.exists():
        raise ValueError("choose a fresh output directory")
    config = {"nodes": 4, "max_depth": 3, "reward_units": 100,
              "unit": "demo-credits", "topology": "publisher -> relay -> relay -> node",
              "transport": "single local HTTP Relay signed public event feed",
              "ttl_seconds": 3600, "decision_epochs": 100,
              "scenarios": ["multilevel", "overspend-rejected", "cycle-rejected", "revocation-cascade"]}
    timings = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sidecar-bounty-demo-") as temporary:
        root = Path(temporary)
        store = RelayStore(root / "relay.db")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        nodes = []
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            nodes = [Sidecar(root / str(i), url) for i in range(4)]
            a, b, c, d = nodes
            for node in nodes:
                node.decisions.configure(relay=True, node=True)

            def act(node, action, data):
                start = time.perf_counter()
                result = node.bounties.act(action, data, f"demo-{len(timings)}")
                if result["status"] != "applied":
                    raise AssertionError(result)
                timings.append({"action": action, "latency_ms": (time.perf_counter() - start) * 1000})
                return result["event_id"]

            bounty = act(a, "publish", dict(title="Compile a research result", task="Split and review the work", reward=100,
                unit="demo-credits", acceptance="Parent checks result artifact", deadline=int(time.time()) + 3600, max_depth=3))
            act(b, "apply", dict(bounty_id=bounty, proposal="Organize the work"))

            def offer(parent, child, parent_id, reward, allow):
                return act(parent, "offer", dict(bounty_id=bounty, parent_id=parent_id, child_id=child.identity.agent_id,
                    child_key=child.identity.public().signing_key, task="Subtask", reward=reward,
                    acceptance="Artifact hash reviewed by parent", allow_relay=allow))

            def accept(parent, child, identifier, role):
                act(child, "respond", dict(offer_id=identifier, decision="accept", role=role,
                    parent_key=parent.identity.public().signing_key))

            first = offer(a, b, "", 100, True)
            accept(a, b, first, "relay")
            second = offer(b, c, first, 60, True)
            accept(b, c, second, "node")
            act(c, "promote", dict(offer_id=second))
            third = offer(c, d, second, 20, False)
            accept(c, d, third, "node")

            checks = {}
            for name, args in (("overspend_rejected", (b, d, first, 50, False)),
                               ("cycle_rejected", (c, a, second, 10, False))):
                try:
                    offer(*args)
                except ValueError:
                    checks[name] = True
                else:
                    raise AssertionError(name)
            snapshots = []
            for node in nodes:
                node.bounties.sync()
                snapshots.append(node.bounties.snapshot())
                assert node.store.trusted_ids() == []
                assert node.store.projects() == []
            assert all(snapshot["offers"] == snapshots[0]["offers"] for snapshot in snapshots)
            checks["converged"] = True
            checks["no_transitive_trust"] = True
            training = []
            for role in ("relay", "node"):
                for value, target in ((0.0, -0.5), (0.5, 0.1), (1.0, 0.7)):
                    b.decisions.record(role, {key: value for key in FEATURES[role]}, target)
                training.append(b.decisions.train(role, epochs=config["decision_epochs"]))
            act(a, "revoke", dict(offer_id=first, reason="Demonstrate cascade"))
            for node in nodes:
                node.bounties.sync()
                assert all(not item["effective_active"] for item in node.bounties.snapshot()["offers"])
            checks["revocation_cascade"] = True
            feed = store.bounty_events(limit=500)
            results = {"config": config, "checks": checks,
                "event_count": len(feed["events"]),
                "signed_event_bytes": sum(len(canonical(item["event"])) for item in feed["events"]),
                "tree_before_revocation": snapshots[0]["offers"], "training": training,
                "timings": timings, "elapsed_ms": (time.perf_counter() - started) * 1000,
                "limits": ["local protocol benchmark, not network scalability evidence",
                           "public rewards are promises, not escrow or payments",
                           "linear heads trained on synthetic labels, not validated model policies"]}
        finally:
            for node in nodes:
                node.store.close()
            server.shutdown()
            server.server_close()
            thread.join()
            store.close()
    output.mkdir(parents=True)
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (output / "summary.md").write_text(
        "# Bounty Protocol Benchmark\n\n"
        + f"Four nodes; three delegation levels; {results['event_count']} signed events; "
        + f"{results['signed_event_bytes']} event bytes (excluding HTTP/polling overhead).\n\n"
        + "Checks: " + ", ".join(results["checks"]) + ". All passed.\n\n"
        + "Reward retention by level: 40 / 40 / 20 demo credits. No payment occurred.\n\n"
        + "Both independent decision heads completed local supervised baseline fitting.\n\n"
        + "Limits: " + "; ".join(results["limits"]) + ".\n")
    return {"output": str(output), "checks": checks, "event_count": results["event_count"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(io.StringIO()):
        result = run(args.output)
    print(json.dumps(result, indent=2))
