"""Owned, offline four-user runtime for the packaged Toy desktop application."""
import json
import secrets
from pathlib import Path
from http.server import ThreadingHTTPServer

from .desktop import DesktopRuntime, desktop_handler
from .relay import make_handler as relay_handler
from .research import ResearchStore, make_research_handler
from .service import Sidecar, make_handler as sidecar_handler
from .store import RelayStore


class ToyDesktopRuntime(DesktopRuntime):
    def __init__(self, data_dir, token):
        super().__init__(data_dir, token)
        self.sidecars = []
        self.users = []
        self.research_store = None

    def start(self):
        try:
            metadata = self.root / "toy-workspace.json"
            if metadata.exists():
                config = json.loads(metadata.read_text("utf-8"))
                if config.get("schema") != "sidecar-toy-desktop/1":
                    raise ValueError("unrecognized desktop workspace")
            else:
                config = {"schema": "sidecar-toy-desktop/1", "experiment_id": "desktop-" + secrets.token_hex(12)}
                metadata.write_text(json.dumps(config), encoding="utf-8")
                metadata.chmod(0o600)
            relay_store = RelayStore(self.root / "relay.db")
            self.stores.append(relay_store)
            relay_token = secrets.token_hex(32)
            relay = ThreadingHTTPServer(("127.0.0.1", 0), relay_handler(relay_store, relay_token))
            self.servers.append(relay)
            self._start_server(relay)
            admin_file = self.root / "research-admin.private"
            if not admin_file.exists():
                admin_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            admin_file.chmod(0o600)
            self.research_store = ResearchStore(self.root / "research.db", config["experiment_id"])
            collector = ThreadingHTTPServer(("127.0.0.1", 0), make_research_handler(self.research_store, admin_file.read_text("utf-8").strip()))
            self.servers.append(collector)
            self._start_server(collector)
            for index, name in enumerate(("Alice", "Bob", "Carol", "Dave")):
                node_root = self.root / ("node-" + str(index))
                node_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                node_config = {"mode": "toy", "experiment_id": config["experiment_id"],
                               "research_url": f"http://127.0.0.1:{collector.server_port}",
                               "summary": {"kind": "demo"}}
                config_file = node_root / "toy-config.json"
                config_file.write_text(json.dumps(node_config), encoding="utf-8")
                config_file.chmod(0o600)
                server = ThreadingHTTPServer(("127.0.0.1", 0), sidecar_handler(None))
                self.servers.append(server)
                url = f"http://127.0.0.1:{server.server_port}"
                node = Sidecar(node_root, f"http://127.0.0.1:{relay.server_port}", relay_token, url, hardware_profile="basic")
                self.sidecars.append(node)
                self.stores.append(node.store)
                server.RequestHandlerClass = desktop_handler(node, self.token, url)
                self._start_server(server)
                self.users.append({"name": name, "url": url})
            self.sidecar = self.sidecars[0]
            self.url = self.users[0]["url"]
            # Loopback ports are ephemeral. Only the owning node can publish a
            # new signed manifest; known local identities do not gain membership.
            local_nodes = {node.identity.agent_id: node for node in self.sidecars}
            for owner in self.sidecars:
                for project in owner.store.projects():
                    if project["status"] != "active" or project["owner_id"] != owner.identity.agent_id:
                        continue
                    changed = False
                    for member in owner.store.project_members(project["project_id"]):
                        node = local_nodes.get(member["agent_id"])
                        if node and member["signing_key"] == node.identity.public().signing_key and member["direct_endpoint"] != node.direct_endpoint:
                            owner.store.set_project_member_endpoint(project["project_id"], member["agent_id"], node.direct_endpoint)
                            changed = True
                    if changed:
                        membership = owner._build_project_manifest(project["project_id"])
                        owner._distribute_project_manifest(project["project_id"], membership)
            for node in self.sidecars:
                node.bounties.start()
                node.toy.start()
            return self.url
        except Exception:
            self.close()
            raise

    def close(self):
        # Stop all clients before their shared Relay/collector and SQLite handles.
        for node in self.sidecars:
            node.bounties.stop()
            node.toy.stop()
        self.sidecar = None
        super().close()
        self.sidecars.clear()
        self.users.clear()
        if self.research_store:
            self.research_store.db.close()
            self.research_store = None
