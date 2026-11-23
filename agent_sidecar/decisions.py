"""Two independent, local, trainable decision heads. Never executes actions."""
from __future__ import annotations

import json
import math
import time
import uuid

FEATURES = {
    "relay": ("capability_match", "reliability", "coverage", "cost", "load", "risk"),
    "node": ("reward", "success_probability", "learning_value", "cost", "duration", "risk"),
}
DEFAULT_WEIGHTS = {
    "relay": [0.3, 0.3, 0.2, -0.2, -0.1, -0.3],
    "node": [0.4, 0.2, 0.1, -0.3, -0.1, -0.3],
}


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("numeric value outside allowed range")
    return float(value)


class DecisionModels:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.executescript("""
                CREATE TABLE IF NOT EXISTS decision_heads (
                    role TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0, weights TEXT NOT NULL,
                    bias REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS decision_examples (
                    example_id TEXT PRIMARY KEY, role TEXT NOT NULL,
                    features TEXT NOT NULL, target REAL NOT NULL, created_at INTEGER NOT NULL
                );
            """)
            for role, weights in DEFAULT_WEIGHTS.items():
                store.db.execute("INSERT OR IGNORE INTO decision_heads(role, weights) VALUES (?, ?)", (role, json.dumps(weights)))

    def _role(self, role):
        if role not in FEATURES:
            raise ValueError("role must be relay or node")
        return role

    def status(self):
        with self.store.lock:
            rows = self.store.db.execute("SELECT * FROM decision_heads ORDER BY role").fetchall()
            return {r["role"]: {"active": bool(r["active"]), "version": r["version"],
                    "features": list(FEATURES[r["role"]]), "weights": json.loads(r["weights"]),
                    "bias": r["bias"], "kind": "local-linear-utility-v1",
                    "examples": self.store.db.execute("SELECT COUNT(*) FROM decision_examples WHERE role=?", (r["role"],)).fetchone()[0]}
                    for r in rows}

    def configure(self, relay, node):
        if type(relay) is not bool or type(node) is not bool:
            raise ValueError("relay and node activation must be booleans")
        with self.store.lock, self.store.db:
            self.store.db.executemany("UPDATE decision_heads SET active=? WHERE role=?", [(relay, "relay"), (node, "node")])
        return self.status()

    def require_active(self, role):
        if not self.status()[self._role(role)]["active"]:
            raise ValueError(role + " decision role is not active")

    def _features(self, role, value):
        self._role(role)
        if not isinstance(value, dict) or set(value) != set(FEATURES[role]):
            raise ValueError("features must exactly match the selected head schema")
        return [number(value[name], 0, 1) for name in FEATURES[role]]

    def record(self, role, features, target):
        values = self._features(role, features)
        target = number(target, -1, 1)
        identifier = uuid.uuid4().hex
        with self.store.lock, self.store.db:
            count = self.store.db.execute("SELECT COUNT(*) FROM decision_examples WHERE role=?", (role,)).fetchone()[0]
            if count >= 10000:
                raise ValueError("decision training example limit reached")
            self.store.db.execute("INSERT INTO decision_examples VALUES (?, ?, ?, ?, ?)", (identifier, role, json.dumps(values), target, int(time.time())))
        return {"example_id": identifier, "role": role}

    def train(self, role, epochs=100, learning_rate=0.05):
        self._role(role)
        if type(epochs) is not int or not 1 <= epochs <= 500:
            raise ValueError("epochs must be an integer from 1 to 500")
        # Six unit-bounded features plus bias: 0.1 stays below the quadratic
        # loss's worst-case stable step size (1/7).
        rate = number(learning_rate, 0.0001, 0.1)
        with self.store.lock, self.store.db:
            rows = self.store.db.execute("SELECT features, target FROM decision_examples WHERE role=? ORDER BY rowid", (role,)).fetchall()
            if len(rows) < 2:
                raise ValueError("at least two explicit local training examples are required")
            data = [(json.loads(r[0]), r[1]) for r in rows]
            head = self.status()[role]
            weights, bias = head["weights"], head["bias"]

            def mse():
                return sum((sum(w * x for w, x in zip(weights, xs)) + bias - y) ** 2 for xs, y in data) / len(data)

            before = mse()
            for _ in range(epochs):
                gradients = [0.0] * len(weights)
                bias_gradient = 0.0
                for xs, target in data:
                    error = sum(w * x for w, x in zip(weights, xs)) + bias - target
                    bias_gradient += error
                    for i, x in enumerate(xs):
                        gradients[i] += error * x
                weights = [w - rate * 2 * g / len(data) for w, g in zip(weights, gradients)]
                bias -= rate * 2 * bias_gradient / len(data)
            after = mse()
            if not math.isfinite(after) or after > before + 1e-12:
                raise ValueError("training did not produce a stable improvement")
            self.store.db.execute("UPDATE decision_heads SET weights=?, bias=?, version=version+1 WHERE role=?", (json.dumps(weights), bias, role))
        return {"role": role, "version": head["version"] + 1, "examples": len(data),
                "training_mse_before": before, "training_mse_after": after,
                "evaluation": "training-set only; no generalization claim"}

    def suggest(self, role, candidates):
        self.require_active(role)
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100:
            raise ValueError("provide 1 to 100 candidates")
        head = self.status()[role]
        scored, ids = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"id", "features"}:
                raise ValueError("candidate requires id and features only")
            identifier = candidate["id"]
            if not isinstance(identifier, str) or not identifier or len(identifier) > 200 or identifier in ids:
                raise ValueError("candidate IDs must be unique nonempty strings")
            ids.add(identifier)
            xs = self._features(role, candidate["features"])
            score = sum(w * x for w, x in zip(head["weights"], xs)) + head["bias"]
            scored.append({"id": identifier, "score": score})
        ranking = sorted(scored, key=lambda row: (-row["score"], row["id"]))
        return {"role": role, "version": head["version"], "advisory_only": True,
                "ranking": ranking,
                "recommended_id": ranking[0]["id"] if ranking[0]["score"] > 0 else None,
                "requires_explicit_action": True}
