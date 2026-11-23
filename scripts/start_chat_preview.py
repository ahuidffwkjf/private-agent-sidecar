"""Start an isolated loopback-only Relay and chat UI using the workspace code."""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_ready(url, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Preview process exited; inspect its log')
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.1)
    raise RuntimeError(f'Preview did not become ready: {url}')


if __name__ == '__main__':
    output = ROOT / 'runs' / f'chat-preview-{time.time_ns()}'
    output.mkdir(parents=True)
    processes = []
    try:
        relay_port = free_port()
        relay_url = f'http://127.0.0.1:{relay_port}'
        with (output / 'relay.log').open('w') as log:
            relay = subprocess.Popen([sys.executable, '-m', 'agent_sidecar', 'relay', '--port', str(relay_port), '--database', str(output / 'relay.db')], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        processes.append(relay)
        wait_ready(relay_url + '/healthz', relay)
        ui_port = free_port()
        ui_url = f'http://127.0.0.1:{ui_port}'
        with (output / 'node.log').open('w') as log:
            node = subprocess.Popen([sys.executable, '-m', 'agent_sidecar', 'serve', '--port', str(ui_port), '--data-dir', str(output / 'node'), '--relay', relay_url], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        processes.append(node)
        wait_ready(ui_url + '/ui', node)
        result = {'url': ui_url + '/ui', 'relay_url': relay_url, 'relay_pid': relay.pid, 'node_pid': node.pid, 'output': str(output)}
        (output / 'preview.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    except Exception:
        for process in processes:
            process.terminate()
            process.wait(timeout=10)
        raise
