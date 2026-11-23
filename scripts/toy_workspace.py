"""Start/stop isolated four-user toy workspaces; never touch existing nodes."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_sidecar.toy_protocol import post


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(output, demo=False):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.chmod(output, 0o700)
    relay_port, collector_port = port(), port()
    token_file = output / "research-admin.private"
    token_file.write_text(secrets.token_urlsafe(48))
    os.chmod(token_file, 0o600)
    experiment = "toy-" + secrets.token_hex(6)
    processes = []

    def launch(name, args):
        with (output / (name + ".log")).open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", *args], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        processes.append({"name": name, "pid": process.pid})
        return process

    def wait(url, process):
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("child exited; inspect local workspace log")
            try:
                with urlopen(url + "/healthz", timeout=1):
                    return
            except OSError:
                time.sleep(.1)
        raise TimeoutError("workspace startup timeout")

    try:
        relay_url = f"http://127.0.0.1:{relay_port}"
        research_url = f"http://127.0.0.1:{collector_port}"
        process = launch("relay", ["agent_sidecar", "relay", "--port", str(relay_port), "--database", str(output / "relay.db")])
        wait(relay_url, process)
        process = launch("research", ["agent_sidecar.research", "--port", str(collector_port), "--database", str(output / "research.db"), "--experiment", experiment, "--admin-token-file", str(token_file)])
        wait(research_url, process)
        users = []
        for index, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            node_dir = output / ("node-" + str(index))
            node_dir.mkdir(mode=0o700)
            config = {"mode": "toy", "experiment_id": experiment, "research_url": research_url,
                      "summary": {"kind": "demo" if demo else "unconfigured"}}
            (node_dir / "toy-config.json").write_text(json.dumps(config, indent=2))
            node_port = port()
            url = f"http://127.0.0.1:{node_port}"
            process = launch("node-" + str(index), ["agent_sidecar", "serve", "--port", str(node_port), "--data-dir", str(node_dir), "--relay", relay_url, "--direct-endpoint", url])
            wait(url, process)
            users.append({"name": name, "url": url, "data_dir": str(node_dir)})
        result = {"output": str(output), "experiment_id": experiment, "relay_url": relay_url,
                  "research_url": research_url, "admin_token_file": str(token_file), "users": users,
                  "processes": processes, "summary": "explicit-demo" if demo else "not-configured"}
        (output / "workspace.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except Exception:
        for process in reversed(processes):
            try:
                os.kill(process["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        raise


def stop(manifest):
    value = json.loads(Path(manifest).read_text())
    stopped = []
    for process in reversed(value["processes"]):
        cmd = subprocess.run(["ps", "-p", str(process["pid"]), "-o", "command="], capture_output=True, text=True).stdout
        if value["output"] not in cmd or "agent_sidecar" not in cmd:
            continue
        os.kill(process["pid"], signal.SIGTERM)
        stopped.append(process["name"])
    return {"stopped": stopped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--demo-summary", action="store_true")
    parser.add_argument("--stop", type=Path)
    args = parser.parse_args()
    if args.stop:
        print(json.dumps(stop(args.stop)))
    elif args.output:
        print(json.dumps(start(args.output, args.demo_summary), indent=2))
    else:
        parser.error("provide --output or --stop")
