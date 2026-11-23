"""Own one Tailcat subprocess and terminate it when its supervisor pipe closes."""
import json
import os
import signal
import subprocess
import sys
import threading


def main():
    command = json.loads(sys.stdin.buffer.readline(65537).decode('utf-8'))
    if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
        raise ValueError('invalid child command')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    def watch_parent():
        # The only writer is the owning supervisor. EOF also covers supervisor SIGKILL.
        for line in sys.stdin.buffer:
            if line.strip() == b'kill':
                if child.poll() is None:
                    child.kill()
                break
        stop.set()

    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **({'umask': 0o077} if os.name != 'nt' else {}))
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        while child.poll() is None and not stop.wait(.1):
            pass
        return child.returncode or 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        raise SystemExit(1)
