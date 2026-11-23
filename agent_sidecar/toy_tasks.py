"""Local task/project bridge, durable summaries and experimental traces."""
import json
from pathlib import Path
import threading
import time
import uuid

from .research import ResearchClient
from .task_summary import summarize, validate_summary
from .toy_protocol import digest, sign_request, text, verify_request


class ToyTasks:
    def __init__(self, sidecar):
        self.sidecar, self.store = sidecar, sidecar.store
        path = self.store.data_dir / "toy-config.json"
        self.config = json.loads(path.read_text()) if path.exists() else {"mode": "standard"}
        if self.config.get("mode") not in {"toy", "standard"}:
            raise ValueError("unknown toy mode")
        if self.config.get("mode") == "toy":
            text(self.config.get("experiment_id"), 100)
            from .toy_protocol import safe_url
            safe_url(self.config["research_url"])
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.last_error = None
        with self.store.lock, self.store.db:
            self.store.db.executescript("""
                CREATE TABLE IF NOT EXISTS toy_local_tasks(task_id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS toy_outbox(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, request TEXT NOT NULL, result TEXT, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS toy_summary_jobs(task_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS toy_local_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            self.store.db.execute("UPDATE toy_summary_jobs SET status='pending' WHERE status='running'")
        self.research = ResearchClient(sidecar, self.config)

    def _request(self, request):
        return self.sidecar.relay._request("POST", "/v1/toy", request)

    def _call(self, op, data=None):
        return self._request(sign_request(self.sidecar.identity, op, data or {}))

    def profile(self):
        with self.store.lock:
            row = self.store.db.execute("SELECT value FROM toy_local_settings WHERE key='profile'").fetchone()
        profile = json.loads(row[0]) if row else None
        return {"profile": profile, "research": self.research.status(), "mode": self.config.get("mode", "standard"),
                "summary_kind": self.config.get("summary", {}).get("kind", "unconfigured"), "error": self.last_error}

    def configure_profile(self, nickname, consent_active):
        if self.config.get("mode") != "toy" or type(consent_active) is not bool:
            raise ValueError("explicit toy configuration and consent required")
        request = sign_request(self.sidecar.identity, "profile", {"nickname": text(nickname, 80),
            "experiment_id": self.config["experiment_id"], "consent_active": consent_active})
        result = self._request(request)
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO toy_local_settings VALUES ('profile',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(result),))
            previous = self.research._consent()
            # Renaming an already consenting user must not invalidate queued
            # events bound to the original consent revision.
            if not (previous and previous["data"]["consent_active"] and consent_active):
                self.research.consent(request)
        self.research.flush()
        return self.profile()

    def require_consent(self):
        if self.config.get("mode") != "toy" or not self.research.status()["consent_active"]:
            raise ValueError("请先确认实验内容采集范围")

    def peer_consent(self, nodes):
        if self.config.get("mode") != "toy":
            return
        self.require_consent()
        profiles = {p["node_id"]: p for p in self._call("profiles")["profiles"]}
        if any(not profiles.get(n, {}).get("consent_active") for n in nodes):
            raise ValueError("所有实验会话参与者均须确认采集范围")

    def trace(self, kind, payload, subjects=None, object_id=None, actor_id=None):
        with self.store.lock, self.store.db:
            self.research.record(kind, payload, subjects, object_id, actor_id)

    def project_guard(self, project_id, peer_id=None, write=True):
        if not project_id.startswith("project-toy-"):
            return
        task = self.detail(project_id.removeprefix("project-"))
        if not task["is_member"] or (write and task["status"] in {"offered", "cancelled", "completed"}):
            raise ValueError("task membership is not active")
        if peer_id and peer_id not in task["member_ids"]:
            raise ValueError("peer is not approved for this task group")

    def act(self, operation, data, request_id=None):
        self.require_consent()
        if operation not in {"publish", "apply", "approve", "reject", "withdraw", "leave", "remove", "subtask", "accept_subtask", "decline_subtask", "invite", "accept_invite", "decline_invite", "submit", "review", "finish", "cancel"}:
            raise ValueError("unknown task action")
        request_id = text(request_id or uuid.uuid4().hex, 128)
        fingerprint = digest({"operation": operation, "data": data})
        with self.lock:
            with self.store.lock:
                row = self.store.db.execute("SELECT * FROM toy_outbox WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise ValueError("request ID conflict")
                if row["status"] == "done":
                    return json.loads(row["result"])
                request = json.loads(row["request"])
            else:
                request = sign_request(self.sidecar.identity, operation, data, request_id)
                with self.store.lock, self.store.db:
                    self.store.db.execute("INSERT INTO toy_outbox VALUES (?,?,?,NULL,'queued')", (request_id, fingerprint, json.dumps(request)))
            try:
                result = self._request(request)
            except (OSError, RuntimeError) as exc:
                # HTTP validation errors are not retried automatically.
                if "HTTP 400" in str(exc) or "HTTP 403" in str(exc):
                    with self.store.lock, self.store.db:
                        self.store.db.execute("UPDATE toy_outbox SET status='rejected' WHERE request_id=?", (request_id,))
                    raise ValueError(str(exc)) from exc
                return {"status": "queued", "request_id": request_id}
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE toy_outbox SET result=?,status='done' WHERE request_id=?", (json.dumps(result), request_id))
                self.research.record("task." + operation, {"input": data, "result": {k: result[k] for k in ("task_id", "project_id", "owner_id", "status", "version") if k in result}}, object_id=result.get("task_id"))
            self.sync()
            return self.detail(result["task_id"])

    def sync(self):
        self.require_consent()
        with self.lock:
            tasks = self._call("list")["tasks"]
            with self.store.lock, self.store.db:
                # Replace the authorized view so revoked visibility never lingers.
                self.store.db.execute("DELETE FROM toy_local_tasks")
                self.store.db.executemany("INSERT INTO toy_local_tasks VALUES (?,?)", [(t["task_id"], json.dumps(t)) for t in tasks])
            for task in tasks:
                try:
                    self._bridge(task)
                except Exception as exc:
                    self.last_error = type(exc).__name__
            return self.list()

    def list(self):
        with self.store.lock:
            tasks = [json.loads(r[0]) for r in self.store.db.execute("SELECT record FROM toy_local_tasks ORDER BY rowid DESC")]
            queued = self.store.db.execute("SELECT count(*) FROM toy_outbox WHERE status='queued'").fetchone()[0]
        for task in tasks:
            project = self.store.project(task["project_id"])
            task["group_ready"] = bool(project and self.store.project_member(task["project_id"], self.sidecar.identity.agent_id))
            task.pop("owner_consent", None)
        return {"tasks": tasks, "queued": queued, "error": self.last_error}

    def detail(self, task_id):
        self.require_consent()
        result = self._call("get", {"task_id": task_id})
        project = self.store.project(result["project_id"])
        result["group_ready"] = bool(project and self.store.project_member(result["project_id"], self.sidecar.identity.agent_id))
        return result

    def _bridge(self, task):
        if task.get("structure_only"):
            return
        me, project_id = self.sidecar.identity.agent_id, task["project_id"]
        project = self.store.project(project_id)
        if task["status"] in {"completed", "cancelled"}:
            if project and self.store.project_active(project_id):
                # Every member reconciles terminal directory state locally;
                # closing must not depend on a remote owner staying online.
                self.sidecar.close_project(project_id, notify_members=False) if task["owner_id"] == me else self.store.close_project(project_id)
            return
        if task["owner_id"] == me and task["status"] != "offered":
            if not project:
                self.sidecar.create_project(task["title"], project_id)
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT OR IGNORE INTO toy_summary_jobs VALUES (?,?,?,'pending',NULL)", (task["task_id"], task["source_hash"], task["version"]))
            for node, application in task["applications"].items():
                if application["status"] == "left":
                    if self.store.project_member(project_id, node):
                        self.sidecar.remove_project_member(project_id, node)
                    continue
                if application["status"] not in {"approved", "joined"}:
                    continue
                consent = application["consent"]
                self._verify_join(task, node, application)
                invitations = self.store.project_invitations("outgoing", "", 500)
                existing = [i for i in invitations if i["project_id"] == project_id and i["invitee_agent_id"] == node and i["status"] in {"pending", "accepted", "completed"}]
                if not existing and not self.store.project_member(project_id, node):
                    self.sidecar.send_project_invitation(project_id, node)
            self.sidecar.poll_project_invitation_notifications()
            self.sidecar.retry_accepted_project_invitations()
        elif task["is_member"]:
            self.sidecar.poll_project_invitation_notifications()
            for invitation in self.store.project_invitations("incoming", "pending", 500):
                if invitation["project_id"] != project_id or invitation["owner_agent_id"] != task["owner_id"]:
                    continue
                consent = task["applications"][me]["consent"]
                self._verify_join(task, me, task["applications"][me])
                self.sidecar.respond_project_invitation(invitation["invitation_id"], "accept")
            if self.store.project_member(project_id, me) and task["applications"][me]["status"] == "approved":
                self._call("joined", {"task_id": task["task_id"]})
        elif project and task["applications"].get(me, {}).get("status") == "left":
            self.store.close_project(project_id)

    def _verify_join(self, task, node, application):
        consent, approval = application["consent"], application["approval"]
        signer = verify_request(consent, fresh=False)
        owner = verify_request(approval, fresh=False)
        if (signer.agent_id != node or owner.agent_id != task["owner_id"]
                or consent["operation"] not in {"apply", "accept_invite"}
                or approval["operation"] not in {"approve", "invite"}
                or consent["data"].get("terms_hash") != task["terms_hash"]
                or consent["data"].get("task_id") != task["task_id"]
                or approval["data"].get("task_id") != task["task_id"]
                or approval["data"].get("node_id") != node):
            raise ValueError("bilateral project join consent mismatch")

    def summary_retry(self, task_id):
        task = self.detail(task_id)
        if task["owner_id"] != self.sidecar.identity.agent_id:
            raise ValueError("summary owner required")
        self._call("summary", {"task_id": task_id, "source_hash": task["source_hash"], "version": task["version"], "summary": {"status": "pending"}})
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO toy_summary_jobs VALUES (?,?,?,'pending',NULL) ON CONFLICT(task_id) DO UPDATE SET status='pending',result=NULL,source_hash=excluded.source_hash,version=excluded.version", (task_id, task["source_hash"], task["version"]))
        return {"status": "pending"}

    def complete_summary(self, task_id, source_hash, version, content):
        task = self.detail(task_id)
        if task["source_hash"] != source_hash or task["version"] != version or task["owner_id"] != self.sidecar.identity.agent_id:
            raise ValueError("summary owner/version mismatch")
        summary = validate_summary({"status": "ready", "content": content, "source": "publisher-harness"})
        result = self._call("summary", {"task_id": task_id, "source_hash": source_hash, "version": version, "summary": summary})
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE toy_summary_jobs SET status='done',result=? WHERE task_id=?", (json.dumps(summary), task_id))
        self.trace("harness.summary", {"input": task["original"], "output": content}, object_id=task_id)
        return result

    def run_summary(self):
        with self.store.lock, self.store.db:
            row = self.store.db.execute("SELECT * FROM toy_summary_jobs WHERE status='pending' LIMIT 1").fetchone()
            if not row:
                return
            self.store.db.execute("UPDATE toy_summary_jobs SET status='running' WHERE task_id=?", (row["task_id"],))
        task = self.detail(row["task_id"])
        if task["summary"]["status"] == "ready":
            result = task["summary"]
        else:
            result = summarize(task, self.config.get("summary", {}))
        with self.store.lock, self.store.db:
            current = self.store.db.execute("SELECT status FROM toy_summary_jobs WHERE task_id=?", (row["task_id"],)).fetchone()
            if not current or current[0] != "running":
                return
            self.store.db.execute("UPDATE toy_summary_jobs SET result=?,status='publishing' WHERE task_id=?", (json.dumps(result), row["task_id"]))
        self._publish_summaries()

    def _publish_summaries(self):
        with self.store.lock:
            rows = self.store.db.execute("SELECT * FROM toy_summary_jobs WHERE status='publishing'").fetchall()
        for row in rows:
            result = json.loads(row["result"])
            self._call("summary", {"task_id": row["task_id"], "source_hash": row["source_hash"], "version": row["version"], "summary": result})
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE toy_summary_jobs SET status='done' WHERE task_id=?", (row["task_id"],))
                self.research.record("harness.summary", result, object_id=row["task_id"])

    def start(self):
        if self.config.get("mode") != "toy" or self.worker:
            return
        self.stop_event.clear()
        def work():
            while not self.stop_event.is_set():
                try:
                    if self.research.status()["consent_active"]:
                        self.sync()
                        with self.store.lock:
                            rows = self.store.db.execute("SELECT * FROM toy_outbox WHERE status='queued' LIMIT 20").fetchall()
                        for row in rows:
                            request = json.loads(row["request"])
                            self.act(request["operation"], request["data"], row["request_id"])
                        self._publish_summaries()
                        self.run_summary()
                    self.research.flush()
                    self.last_error = None
                except Exception as exc:
                    self.last_error = type(exc).__name__
                self.stop_event.wait(2)
        self.worker = threading.Thread(target=work, name="toy-tasks", daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join()
            self.worker = None
