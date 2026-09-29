"""Run an in-process Alice/Bob demonstration without leaving data behind."""

import json
import sqlite3
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_sidecar.relay import make_handler
from agent_sidecar.service import Sidecar
from agent_sidecar.store import RelayStore


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        relay_store = RelayStore(root / "relay.db")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay_store))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        relay_url = "http://127.0.0.1:%d" % server.server_port
        try:
            alice = Sidecar(root / "alice", relay_url)
            bob = Sidecar(root / "bob", relay_url)
            alice.trust(bob.identity.agent_id, bob.identity.public().signing_key)
            bob.trust(alice.identity.agent_id, alice.identity.public().signing_key)

            secret = "只允许 Bob 看到的测试任务"
            message_id = alice.send(bob.identity.agent_id, "task.request", {"task": secret})

            database = sqlite3.connect(str(root / "relay.db"))
            stored = database.execute(
                "SELECT envelope_json FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()[0]
            assert secret not in stored

            received = bob.receive()
            assert received[0]["payload"]["task"] == secret
            assert bob.receive() == []

            print("Alice:", alice.identity.agent_id)
            print("Bob:  ", bob.identity.agent_id)
            print("中继数据库未出现明文:", secret not in stored)
            print("Bob 解密收到:")
            print(json.dumps(received, ensure_ascii=False, indent=2))
            print("ACK 后再次拉取:", bob.receive())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    main()
