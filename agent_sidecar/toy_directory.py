"""Toy task directory: authenticated metadata, never group message storage.

This single-server experiment coordinator is a trusted metadata service. Signed
requests preserve consent evidence; it is not Byzantine consensus.
"""
import json
import time

from .toy_protocol import digest, text, verify_request

TERMINAL = {"completed", "cancelled"}


class TaskDirectory:
    def __init__(self, relay_store):
        self.store = relay_store
        self.db = relay_store.db
        with relay_store.lock, self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS toy_profiles(node_id TEXT PRIMARY KEY, profile TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS toy_tasks(task_id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS toy_requests(node_id TEXT, request_id TEXT, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(node_id,request_id));
                CREATE TABLE IF NOT EXISTS toy_task_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL, request_json TEXT NOT NULL, created_at INTEGER NOT NULL);
            """)

    def profiles(self):
        return {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT * FROM toy_profiles")}

    def tasks(self):
        return {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT * FROM toy_tasks")}

    def _profile(self, profiles, node):
        profile = profiles.get(node)
        if not profile or not profile["consent_active"]:
            raise ValueError("experiment consent is required")
        return profile

    def _same_experiment(self, profiles, a, b):
        if self._profile(profiles, a)["experiment_id"] != self._profile(profiles, b)["experiment_id"]:
            raise ValueError("different experiment")

    def _member(self, task, node):
        return node == task["owner_id"] or task["applications"].get(node, {}).get("status") in {"approved", "joined"}

    def _visible(self, task, node, tasks):
        if not task["parent_id"]:
            return True
        # The creator needs progress; other members need explicit membership or invitation.
        if node in {task["creator_id"], task["owner_id"]} or node in task["applications"]:
            return True
        parent = task["parent_id"]
        while parent:
            if tasks[parent]["owner_id"] == node:
                return True
            parent = tasks[parent]["parent_id"]
        return False

    def _task(self, tasks, task_id):
        if task_id not in tasks:
            raise ValueError("task not found")
        return tasks[task_id]

    def _open(self, task):
        if task["status"] in TERMINAL or (task.get("deadline") and int(time.time()) >= task["deadline"]):
            raise ValueError("task is closed or expired")

    def _owner(self, task, node):
        if task["owner_id"] != node:
            raise ValueError("task owner required")

    def _terms(self, task):
        return digest({k: task[k] for k in ("task_id", "version", "project_id", "owner_id", "original", "acceptance", "experiment_id")})

    def _snapshot(self, task, node, profiles):
        result = dict(task)
        result["owner_name"] = profiles.get(task["owner_id"], {}).get("nickname", task["owner_id"])
        if node != task["owner_id"]:
            result["applications"] = {node: task["applications"][node]} if node in task["applications"] else {}
        result["terms_hash"] = self._terms(task)
        result["is_member"] = self._member(task, node)
        result["structure_only"] = bool(task["parent_id"] and not result["is_member"] and node != task["creator_id"] and node not in task["applications"])
        if result["is_member"]:
            result["member_ids"] = [task["owner_id"]] + [n for n,a in task["applications"].items() if a["status"] in {"approved", "joined"}]
        else:
            result["member_ids"] = []
            if node != task["creator_id"]:
                result["submissions"] = []
                result["reviews"] = []
        if result["structure_only"]:
            result.update(original="仅可查看任务层级与进度", acceptance="仅项目成员可见", summary={"status": "restricted"}, applications={}, submissions=[], reviews=[])
            result.pop("publication_proof", None)
            result.pop("owner_consent", None)
        # Raw signed consent is needed only by the applicant/owner bridge.
        return result

    def handle(self, request):
        identity = verify_request(request, fresh=False)
        node = identity.agent_id
        registered = self.store.identity(node)
        if not registered or registered.as_dict() != identity.as_dict():
            raise ValueError("registered identity required")
        op, data = request["operation"], request["data"]
        fingerprint = digest({"operation": op, "data": data})
        with self.store.lock, self.db:
            previous = self.db.execute("SELECT fingerprint,result FROM toy_requests WHERE node_id=? AND request_id=?", (node, request["request_id"])).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise ValueError("request ID conflict")
                return json.loads(previous[1])
            verify_request(request)
            profiles, tasks = self.profiles(), self.tasks()
            if op == "profile":
                if set(data) != {"nickname", "experiment_id", "consent_active"} or type(data["consent_active"]) is not bool:
                    raise ValueError("explicit consent boolean required")
                if node in profiles and profiles[node]["experiment_id"] != data["experiment_id"]:
                    raise ValueError("use a separate node directory for another experiment")
                result = {"user_id": node, "node_id": node, "nickname": text(data["nickname"], 80),
                          "experiment_id": text(data["experiment_id"], 100), "consent_active": data["consent_active"],
                          "consent_request": request}
                self.db.execute("INSERT INTO toy_profiles VALUES (?,?) ON CONFLICT(node_id) DO UPDATE SET profile=excluded.profile", (node, json.dumps(result)))
            elif op == "profiles":
                profile = self._profile(profiles, node)
                result = {"profiles": [{k: p[k] for k in ("node_id", "user_id", "nickname", "experiment_id", "consent_active")} for p in profiles.values() if p["experiment_id"] == profile["experiment_id"]]}
            else:
                profile = self._profile(profiles, node)
                if op == "list":
                    result = {"tasks": [self._snapshot(t, node, profiles) for t in tasks.values()
                                        if t["experiment_id"] == profile["experiment_id"] and self._visible(t, node, tasks)]}
                elif op == "publish":
                    task_id = "toy-" + digest({"node": node, "request_id": request["request_id"]})[:32]
                    depth = data.get("max_depth", 3)
                    if type(depth) is not int or not 1 <= depth <= 8:
                        raise ValueError("max depth must be 1..8")
                    task = self._new(task_id, node, node, "", data, profile["experiment_id"], 1, depth)
                    task["publication_proof"] = request
                    tasks[task_id] = task
                    result = self._snapshot(task, node, profiles)
                else:
                    task = self._task(tasks, data.get("task_id"))
                    if task["experiment_id"] != profile["experiment_id"] or not self._visible(task, node, tasks):
                        raise ValueError("task not visible")
                    result = self._action(op, data, task, tasks, profiles, node, request)
                if op not in {"list", "get"}:
                    for task in tasks.values():
                        self.db.execute("INSERT INTO toy_tasks VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET record=excluded.record", (task["task_id"], json.dumps(task)))
            if op not in {"list", "get", "profiles"}:
                self.db.execute("INSERT INTO toy_task_audit(node_id,request_json,created_at) VALUES (?,?,?)", (node, json.dumps(request), int(time.time())))
                self.db.execute("INSERT INTO toy_requests VALUES (?,?,?,?)", (node, request["request_id"], fingerprint, json.dumps(result)))
            return result

    def _new(self, identifier, owner, creator, parent, data, experiment, depth, max_depth):
        original = text(data.get("original"))
        acceptance = text(data.get("acceptance", "未说明"), 4000)
        return {"task_id": identifier, "project_id": "project-" + identifier, "parent_id": parent,
                "owner_id": owner, "creator_id": creator, "experiment_id": experiment,
                "title": text(data.get("title"), 200), "original": original, "acceptance": acceptance,
                "version": 1, "source_hash": digest({"original": original, "acceptance": acceptance}),
                "depth": depth, "max_depth": max_depth, "created_at": int(time.time()),
                "status": "offered" if parent else "open", "summary": {"status": "pending"},
                "applications": {}, "submissions": [], "reviews": []}

    def _action(self, op, data, task, tasks, profiles, node, request):
        if op == "get":
            return self._snapshot(task, node, profiles)
        if op == "summary":
            self._owner(task, node)
            if data.get("source_hash") != task["source_hash"] or data.get("version") != task["version"]:
                raise ValueError("stale summary")
            from .task_summary import validate_summary
            task["summary"] = validate_summary(data["summary"])
            task["summary"]["proof"] = request
            return self._snapshot(task, node, profiles)
        if not self._member(task, node) and node != task["creator_id"] and task["parent_id"] and node not in task["applications"]:
            raise ValueError("only hierarchy metadata is visible")
        self._open(task)
        if op == "apply":
            if task["parent_id"]:
                raise ValueError("subtasks are invitation only")
            if node == task["owner_id"]:
                raise ValueError("already task owner")
            if data.get("terms_hash") != self._terms(task):
                raise ValueError("task terms changed")
            previous = task["applications"].get(node)
            if previous and previous["status"] in {"pending", "approved", "joined"}:
                return self._snapshot(task, node, profiles)
            task["applications"][node] = {"status": "pending", "proposal": text(data.get("proposal", "愿意参加"), 2000), "consent": request}
        elif op in {"approve", "reject"}:
            self._owner(task, node)
            target = data["node_id"]
            self._same_experiment(profiles, node, target)
            app = task["applications"].get(target)
            if not app or app["status"] not in {"pending", "approved", "joined"}:
                raise ValueError("no pending application")
            if app["status"] in {"approved", "joined"}:
                if op == "reject":
                    raise ValueError("use removal for joined members")
                return self._snapshot(task, node, profiles)
            app["status"] = "approved" if op == "approve" else "rejected"
            app["approval"] = request
        elif op == "withdraw":
            app = task["applications"].get(node)
            if not app or app["status"] != "pending":
                raise ValueError("only pending applications can be withdrawn")
            app["status"] = "withdrawn"
        elif op == "joined":
            app = task["applications"].get(node)
            if not app or app["status"] not in {"approved", "joined"}:
                raise ValueError("approval required")
            app["status"] = "joined"
        elif op == "subtask":
            self._owner(task, node)
            owner = data["owner_id"]
            self._same_experiment(profiles, node, owner)
            if not self._member(task, owner) or task["depth"] >= task["max_depth"]:
                raise ValueError("owner must be a parent member; depth limit applies")
            identifier = "toy-" + digest({"node": node, "request_id": request["request_id"]})[:32]
            child = self._new(identifier, owner, node, task["task_id"], data, task["experiment_id"], task["depth"] + 1, task["max_depth"])
            child["publication_proof"] = request
            tasks[identifier] = child
            return self._snapshot(child, node, profiles)
        elif op == "accept_subtask":
            self._owner(task, node)
            if task["status"] != "offered" or not self._member(tasks[task["parent_id"]], node) or data.get("terms_hash") != self._terms(task):
                raise ValueError("subtask offer is not valid")
            task["status"] = "open"
            task["owner_consent"] = request
        elif op == "decline_subtask":
            self._owner(task, node)
            if task["status"] != "offered":
                raise ValueError("only an unaccepted subtask can be declined")
            task["status"] = "cancelled"
        elif op == "invite":
            self._owner(task, node)
            if task["status"] == "offered":
                raise ValueError("only active groups use directed invitations")
            target = data["node_id"]
            self._same_experiment(profiles, node, target)
            if target == node or (task["parent_id"] and not self._member(tasks[task["parent_id"]], target)):
                raise ValueError("invitee must be a direct parent group member")
            if target not in task["applications"] or task["applications"][target]["status"] in {"rejected", "left"}:
                task["applications"][target] = {"status": "invited", "invitation": request}
        elif op == "accept_invite":
            app = task["applications"].get(node)
            if not app or app["status"] != "invited" or data.get("terms_hash") != self._terms(task) or (task["parent_id"] and not self._member(tasks[task["parent_id"]], node)):
                raise ValueError("invalid task invitation")
            app.update(status="approved", consent=request, approval=app["invitation"])
        elif op == "decline_invite":
            app = task["applications"].get(node)
            if not app or app["status"] != "invited":
                raise ValueError("no pending subgroup invitation")
            app["status"] = "rejected"
        elif op in {"leave", "remove"}:
            target = node if op == "leave" else data["node_id"]
            if op == "remove":
                self._owner(task, node)
            affected = [t for t in tasks.values() if self._descendant(t, task["task_id"], tasks)]
            if any(t["owner_id"] == target and t["status"] not in TERMINAL for t in [task] + affected):
                raise ValueError("close owned branches before leaving")
            for current in [task] + affected:
                if target in current["applications"]:
                    current["applications"][target]["status"] = "left"
        elif op == "submit":
            if not self._member(task, node) or task["status"] == "offered":
                raise ValueError("membership required")
            if task["parent_id"] and node != task["owner_id"]:
                raise ValueError("subtask owner submits to parent")
            if any(t["parent_id"] == task["task_id"] and t["status"] not in TERMINAL for t in tasks.values()):
                raise ValueError("resolve child work first")
            task["submissions"].append({"submission_id": request["request_id"], "node_id": node,
                "text": text(data["text"]), "reference": str(data.get("reference", ""))[:2000], "status": "pending"})
        elif op == "review":
            reviewer = task["creator_id"] if task["parent_id"] else task["owner_id"]
            if reviewer != node:
                raise ValueError("direct task supervisor required")
            submission = next((s for s in task["submissions"] if s["submission_id"] == data["submission_id"]), None)
            if not submission or submission["status"] != "pending" or data["decision"] not in {"accept", "reject"}:
                raise ValueError("invalid review")
            submission["status"] = "accepted" if data["decision"] == "accept" else "rejected"
            task["reviews"].append({"submission_id": data["submission_id"], "decision": data["decision"], "reason": text(data.get("reason", "已审核"), 2000)})
            if task["parent_id"] and data["decision"] == "accept":
                task["status"] = "completed"
        elif op in {"finish", "cancel"}:
            if node not in {task["owner_id"], task["creator_id"]}:
                raise ValueError("supervisor required")
            children = [t for t in tasks.values() if self._descendant(t, task["task_id"], tasks)]
            if op == "finish":
                if any(t["status"] not in TERMINAL for t in children) or not any(s["status"] == "accepted" for s in task["submissions"]):
                    raise ValueError("review accepted results and resolve children first")
                task["status"] = "completed"
            else:
                for current in [task] + children:
                    if current["status"] not in TERMINAL:
                        current["status"] = "cancelled"
        else:
            raise ValueError("unknown task operation")
        return self._snapshot(task, node, profiles)

    def _descendant(self, task, ancestor, tasks):
        parent = task["parent_id"]
        while parent:
            if parent == ancestor:
                return True
            parent = tasks[parent]["parent_id"]
        return False
