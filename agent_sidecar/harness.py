from __future__ import annotations

import hashlib
import json
import math
import subprocess
import shutil
import tempfile
import time
import uuid
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol

from .federated.types import (
    EvaluateRequest,
    EvaluateResult,
    HarnessDescriptor,
    ModelArtifact,
    ModelState,
    TrainRequest,
    TrainResult,
    UpdateArtifact,
    canonical_json,
    clone_model,
    model_hash,
)


class HarnessAdapter(Protocol):
    name: str
    version: str

    def describe(self) -> HarnessDescriptor: ...
    def load_global(self, model: ModelArtifact) -> None: ...
    def train(self, request: TrainRequest) -> TrainResult: ...
    def evaluate(self, request: EvaluateRequest) -> EvaluateResult: ...
    def export_update(self, result: TrainResult, update_id: str, round_id: str, model_id: str) -> UpdateArtifact: ...
    def import_global(self, artifact: ModelArtifact) -> None: ...


def encode_model(model: ModelState) -> bytes:
    return canonical_json(model)


def decode_model(payload: bytes) -> ModelState:
    value = json.loads(payload.decode("utf-8"))
    return {str(name): [float(item) for item in values] for name, values in value.items()}


class ToyHarness:
    """Pure-Python logistic regression Harness used by demos, tests and CI."""

    name = "toy"
    version = "1.0"

    def __init__(self, data: Iterable[tuple[list[float], int]]):
        self.data = list(data)
        if not self.data:
            raise ValueError("toy harness requires non-empty data")
        width = len(self.data[0][0])
        if width == 0 or any(len(features) != width for features, _ in self.data):
            raise ValueError("toy features must have one consistent non-empty width")

    def describe(self) -> HarnessDescriptor:
        return HarnessDescriptor(
            name=self.name,
            version=self.version,
            model_family="logistic-regression",
            supported_tasks=("binary-classification",),
            supported_update_formats=("json-vector",),
            capabilities={"train": True, "evaluate": True, "resume": True},
        )

    def load_global(self, model: ModelArtifact) -> None:
        if model.model_hash != model_hash(decode_model(model.payload)):
            raise ValueError("model artifact hash mismatch")

    def import_global(self, artifact: ModelArtifact) -> None:
        self.load_global(artifact)

    @staticmethod
    def initial_model(width: int = 2) -> ModelState:
        return {"weights": [0.0] * width, "bias": [0.0]}

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-60.0, min(60.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def train(self, request: TrainRequest) -> TrainResult:
        started = time.perf_counter()
        model = clone_model(request.global_model)
        if "weights" not in model or "bias" not in model or len(model["bias"]) != 1:
            raise ValueError("toy harness expects weights and scalar bias")
        width = len(model["weights"])
        if any(len(features) != width for features, _ in self.data):
            raise ValueError("toy model width does not match data")
        steps = max(1, int(request.local_steps))
        lr = float(request.learning_rate)
        mu = float(request.proximal_mu)
        for _ in range(steps):
            grad_w = [0.0] * width
            grad_b = 0.0
            loss = 0.0
            for features, label in self.data:
                logit = sum(w * x for w, x in zip(model["weights"], features)) + model["bias"][0]
                prediction = self._sigmoid(logit)
                error = prediction - float(label)
                loss += -(label * math.log(max(prediction, 1e-12)) + (1 - label) * math.log(max(1 - prediction, 1e-12)))
                for i, feature in enumerate(features):
                    grad_w[i] += error * feature
                grad_b += error
            n = float(len(self.data))
            for i in range(width):
                proximal = mu * (model["weights"][i] - request.global_model["weights"][i])
                model["weights"][i] -= lr * (grad_w[i] / n + proximal)
            proximal_b = mu * (model["bias"][0] - request.global_model["bias"][0])
            model["bias"][0] -= lr * (grad_b / n + proximal_b)
        eval_result = self.evaluate(EvaluateRequest(request.model_id, model_hash(model), model))
        return TrainResult(model, len(self.data), (time.perf_counter() - started) * 1000, eval_result.metrics, {"loss": loss / len(self.data)})

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        started = time.perf_counter()
        correct = 0
        loss = 0.0
        for features, label in self.data:
            logit = sum(w * x for w, x in zip(request.model["weights"], features)) + request.model["bias"][0]
            prediction = self._sigmoid(logit)
            predicted = 1 if prediction >= 0.5 else 0
            correct += int(predicted == label)
            loss += -(label * math.log(max(prediction, 1e-12)) + (1 - label) * math.log(max(1 - prediction, 1e-12)))
        n = float(len(self.data))
        return EvaluateResult({"accuracy": correct / n, "loss": loss / n}, (time.perf_counter() - started) * 1000)

    def export_update(self, result: TrainResult, update_id: str, round_id: str, model_id: str) -> UpdateArtifact:
        payload = encode_model(result.model)
        return UpdateArtifact(update_id, round_id, model_id, model_hash(result.model), "json-vector", payload, hashlib.sha256(payload).hexdigest())


class CommandHarnessAdapter:
    """Adapter for external harnesses that exchange JSON files with commands."""

    name = "command"
    version = "1.0"

    def __init__(self, train_command: List[str], evaluate_command: Optional[List[str]] = None, cwd: Optional[str] = None):
        self.train_command = train_command
        self.evaluate_command = evaluate_command
        self.cwd = cwd

    def describe(self) -> HarnessDescriptor:
        return HarnessDescriptor(name=self.name, version=self.version, runtime="command", supported_update_formats=("json-vector", "safetensors"), capabilities={"train": True, "evaluate": bool(self.evaluate_command)})

    def load_global(self, model: ModelArtifact) -> None:
        return None

    def import_global(self, artifact: ModelArtifact) -> None:
        return None

    def _run(self, command: List[str], request: Mapping[str, Any], timeout: float = 300.0) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="sidecar-harness-") as directory:
            input_path = Path(directory) / "request.json"
            output_path = Path(directory) / "response.json"
            input_path.write_bytes(canonical_json(request))
            rendered = [item.replace("{input}", str(input_path)).replace("{output}", str(output_path)) for item in command]
            completed = subprocess.run(rendered, cwd=self.cwd, capture_output=True, text=True, timeout=timeout)
            if completed.returncode != 0:
                raise RuntimeError(f"harness command failed: {completed.returncode}: {completed.stderr[-1000:]}")
            if not output_path.exists():
                raise RuntimeError("harness command did not create response file")
            return json.loads(output_path.read_text("utf-8"))

    def train(self, request: TrainRequest) -> TrainResult:
        started = time.perf_counter()
        response = self._run(self.train_command, {"request": asdict(request)})
        return TrainResult(response["model"], int(response["num_examples"]), (time.perf_counter() - started) * 1000, response.get("metrics", {}), response.get("metadata", {}))

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        if not self.evaluate_command:
            raise RuntimeError("command harness has no evaluate command")
        started = time.perf_counter()
        response = self._run(self.evaluate_command, {"request": asdict(request)})
        return EvaluateResult(response.get("metrics", {}), (time.perf_counter() - started) * 1000)

    def export_update(self, result: TrainResult, update_id: str, round_id: str, model_id: str) -> UpdateArtifact:
        payload = encode_model(result.model)
        return UpdateArtifact(update_id, round_id, model_id, model_hash(result.model), "json-vector", payload, hashlib.sha256(payload).hexdigest())


class HttpHarnessAdapter:
    name = "http"
    version = "1.0"

    def __init__(self, base_url: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        payload = None if body is None else canonical_json(body)
        request = urllib.request.Request(self.base_url + path, data=payload, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"harness returned HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc

    def describe(self) -> HarnessDescriptor:
        value = self._request("GET", "/v1/harness/describe")
        return HarnessDescriptor(**value)

    def load_global(self, model: ModelArtifact) -> None:
        self._request("POST", "/v1/harness/import-global", {"model_id": model.model_id, "model_hash": model.model_hash, "format": model.format, "payload": model.payload.decode("utf-8", errors="ignore")})

    def import_global(self, artifact: ModelArtifact) -> None:
        self.load_global(artifact)

    def train(self, request: TrainRequest) -> TrainResult:
        value = self._request("POST", "/v1/harness/train", {"request": asdict(request)})
        return TrainResult(value["model"], int(value["num_examples"]), float(value.get("train_ms", 0)), value.get("metrics", {}), value.get("metadata", {}))

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        value = self._request("POST", "/v1/harness/evaluate", {"request": asdict(request)})
        return EvaluateResult(value.get("metrics", {}), float(value.get("eval_ms", 0)))

    def export_update(self, result: TrainResult, update_id: str, round_id: str, model_id: str) -> UpdateArtifact:
        payload = encode_model(result.model)
        return UpdateArtifact(update_id, round_id, model_id, model_hash(result.model), "json-vector", payload, hashlib.sha256(payload).hexdigest())


class DeepSeekHarnessAdapter(CommandHarnessAdapter):
    name = "deepseek"
    version = "0.1"

    def describe(self) -> HarnessDescriptor:
        return HarnessDescriptor(name=self.name, version=self.version, runtime="command", model_family="deepseek", supported_tasks=("instruction-tuning",), supported_update_formats=("safetensors", "json-vector"), capabilities={"train": True, "evaluate": bool(self.evaluate_command), "lora": True})


class DshControllerAdapter:
    """Client for the existing DeepSeek Harness controller.

    This adapter intentionally treats the DSH + MicroVM controller as an opaque
    Harness. It uses the controller's public task API and never imports DSH
    internals, so the federated platform can later add another controller with
    the same task contract.
    """

    name = "deepseek-controller"
    version = "1.0"

    def __init__(self, controller_url: str, registry_token: str, timeout: float = 900.0, poll_interval: float = 1.0):
        self.controller_url = controller_url.rstrip("/")
        self.registry_token = registry_token
        self.timeout = timeout
        self.poll_interval = poll_interval

    def _request(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        payload = None if body is None else canonical_json(body)
        headers = {"Accept": "application/json", "X-Registry-Token": self.registry_token}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.controller_url + path, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 30.0)) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"dsh controller returned HTTP {exc.code}: {detail}") from exc

    def describe(self) -> HarnessDescriptor:
        return HarnessDescriptor(
            name=self.name,
            version=self.version,
            runtime="dsh-controller",
            model_family="deepseek",
            supported_tasks=("general-task", "instruction-execution"),
            supported_update_formats=("json-vector", "safetensors"),
            capabilities={"task_execution": True, "microvm": True, "polling": True, "federated_training": False},
        )

    def execute_task(self, description: str, task_id: Optional[str] = None, required_capabilities: Optional[List[str]] = None, required_tools: Optional[List[str]] = None) -> Dict[str, Any]:
        if not description or not description.strip():
            raise ValueError("description is required")
        created = self._request("POST", "/tasks", {
            "task_id": task_id or uuid.uuid4().hex,
            "description": description,
            "required_capabilities": required_capabilities or [],
            "required_tools": required_tools or [],
        })
        job_id = str(created.get("job_id", ""))
        if not job_id:
            raise RuntimeError("dsh controller did not return job_id")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            job = self._request("GET", "/tasks/" + urllib.parse.quote(job_id, safe=""))
            status = job.get("status")
            if status == "completed":
                return dict(job.get("result") or {})
            if status == "failed":
                result = dict(job.get("result") or {})
                result.setdefault("success", False)
                result.setdefault("failure_type", "dsh_controller_job_failed")
                return result
            time.sleep(self.poll_interval)
        raise TimeoutError("dsh controller task timed out")


class DshControllerTrainingAdapter(DshControllerAdapter):
    """Structured federated-training bridge over the DSH task controller.

    The DSH controller remains task-oriented. A DSH Harness participating in FL
    must return a JSON object in its task result with `model`, `num_examples`
    and optional `metrics` for training, or `metrics` for evaluation. The
    adapter fails closed when the task output is not structured JSON.
    """

    name = "deepseek-controller-training"
    version = "1.0"

    def __init__(self, controller_url: str, registry_token: str, timeout: float = 900.0, poll_interval: float = 1.0, task_capabilities: Optional[List[str]] = None, task_tools: Optional[List[str]] = None):
        super().__init__(controller_url, registry_token, timeout, poll_interval)
        self.task_capabilities = task_capabilities or ["python"]
        self.task_tools = task_tools or ["dsh", "python"]

    def describe(self) -> HarnessDescriptor:
        return HarnessDescriptor(
            name=self.name,
            version=self.version,
            runtime="dsh-controller",
            model_family="deepseek",
            supported_tasks=("federated-train", "federated-evaluate"),
            supported_update_formats=("json-vector", "safetensors"),
            capabilities={"train": True, "evaluate": True, "task_execution": True, "microvm": True, "structured_result": True, "lora": True},
        )

    @staticmethod
    def _structured_result(result: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(result.get("structured_result"), dict):
            return dict(result["structured_result"])
        if isinstance(result.get("output"), dict):
            return dict(result["output"])
        output = result.get("output")
        if isinstance(output, str):
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError as exc:
                raise RuntimeError("DSH task output is not valid JSON; expected structured_result") from exc
            if isinstance(parsed, dict):
                return parsed
        raise RuntimeError("DSH task did not return structured_result")

    def _run_structured(self, operation: str, request: Mapping[str, Any]) -> Dict[str, Any]:
        description = "Execute federated operation and return JSON only. " + json.dumps({"operation": operation, "request": request}, ensure_ascii=False, sort_keys=True)
        result = self.execute_task(description, required_capabilities=self.task_capabilities, required_tools=self.task_tools)
        if not result.get("success", True):
            raise RuntimeError(str(result.get("error") or result.get("failure_type") or "DSH federated task failed"))
        return self._structured_result(result)

    def load_global(self, model: ModelArtifact) -> None:
        return None

    def import_global(self, artifact: ModelArtifact) -> None:
        return None

    def train(self, request: TrainRequest) -> TrainResult:
        started = time.perf_counter()
        response = self._run_structured("train", asdict(request))
        if "model" not in response or "num_examples" not in response:
            raise RuntimeError("structured DSH train result requires model and num_examples")
        return TrainResult(response["model"], int(response["num_examples"]), float(response.get("train_ms", (time.perf_counter() - started) * 1000)), response.get("metrics", {}), response.get("metadata", {}))

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        started = time.perf_counter()
        response = self._run_structured("evaluate", asdict(request))
        if not isinstance(response.get("metrics"), dict):
            raise RuntimeError("structured DSH evaluate result requires metrics")
        return EvaluateResult(response["metrics"], float(response.get("eval_ms", (time.perf_counter() - started) * 1000)))

    def export_update(self, result: TrainResult, update_id: str, round_id: str, model_id: str) -> UpdateArtifact:
        payload = encode_model(result.model)
        return UpdateArtifact(update_id, round_id, model_id, model_hash(result.model), "json-vector", payload, hashlib.sha256(payload).hexdigest())


class DshHeadlessAdapter:
    """Direct adapter for the installed `dsh --profile headless` executable."""

    name = "deepseek-headless"
    version = "1.0"

    def __init__(self, dsh_bin: str = "dsh", profile: str = "headless", timeout: float = 900.0, permission_mode: str = "workspace-write", telemetry_mode: str = "DISABLED"):
        self.dsh_bin = dsh_bin
        self.profile = profile
        self.timeout = timeout
        self.permission_mode = permission_mode
        self.telemetry_mode = telemetry_mode

    def describe(self) -> HarnessDescriptor:
        return HarnessDescriptor(name=self.name, version=self.version, runtime="dsh", model_family="deepseek", supported_tasks=("federated-train", "federated-evaluate", "general-task"), supported_update_formats=("json-vector", "safetensors"), capabilities={"train": True, "evaluate": True, "task_execution": True, "structured_result": True, "lora": True})

    def _execute(self, operation: str, request: Mapping[str, Any]) -> Dict[str, Any]:
        prompt = "Return JSON only for federated operation. " + json.dumps({"operation": operation, "request": request}, ensure_ascii=False, sort_keys=True)
        env = dict(__import__("os").environ)
        env["DSH_PERMISSION_MODE"] = self.permission_mode
        env["DSH_TELEMETRY_MODE"] = self.telemetry_mode
        try:
            completed = subprocess.run([self.dsh_bin, "--profile", self.profile, prompt], capture_output=True, text=True, timeout=self.timeout, env=env, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError(f"dsh executable not found: {self.dsh_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("dsh headless task timed out") from exc
        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0:
            raise RuntimeError(f"dsh headless failed ({completed.returncode}): {output[-1000:]}")
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("dsh headless returned non-JSON output; structured federated result is required") from exc
        if not isinstance(value, dict):
            raise RuntimeError("dsh headless structured result must be a JSON object")
        return value

    @staticmethod
    def _parse_result(value: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(value.get("structured_result"), dict):
            return dict(value["structured_result"])
        return value

    def load_global(self, model: ModelArtifact) -> None:
        return None

    def import_global(self, artifact: ModelArtifact) -> None:
        return None

    def train(self, request: TrainRequest) -> TrainResult:
        started = time.perf_counter()
        value = self._parse_result(self._execute("train", asdict(request)))
        if "model" not in value or "num_examples" not in value:
            raise RuntimeError("dsh headless train result requires model and num_examples")
        return TrainResult(value["model"], int(value["num_examples"]), float(value.get("train_ms", (time.perf_counter() - started) * 1000)), value.get("metrics", {}), value.get("metadata", {}))

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        started = time.perf_counter()
        value = self._parse_result(self._execute("evaluate", asdict(request)))
        if not isinstance(value.get("metrics"), dict):
            raise RuntimeError("dsh headless evaluate result requires metrics")
        return EvaluateResult(value["metrics"], float(value.get("eval_ms", (time.perf_counter() - started) * 1000)))

    def export_update(self, result: TrainResult, update_id: str, round_id: str, model_id: str) -> UpdateArtifact:
        payload = encode_model(result.model)
        return UpdateArtifact(update_id, round_id, model_id, model_hash(result.model), "json-vector", payload, hashlib.sha256(payload).hexdigest())



def diagnose_dsh(dsh_bin: str = "dsh", profile: str = "headless") -> Dict[str, Any]:
    """Return non-secret local diagnostics for a DSH Harness installation."""
    executable = shutil.which(dsh_bin)
    dsh_home = Path(__import__("os").environ.get("DSH_HOME", str(Path.home() / ".dsh"))).expanduser()
    profile_dir = dsh_home / "profiles" / profile
    credentials_file = dsh_home / ".credentials.yaml"
    credentials_mode = None
    credentials_route_hint = False
    credentials_key_ref_present = False
    if credentials_file.exists():
        try:
            credentials_mode = oct(credentials_file.stat().st_mode & 0o777)
            credential_text = credentials_file.read_text(errors="replace")
            credentials_route_hint = "deepseek-official" in credential_text
            credentials_key_ref_present = bool(re.search(r"(?m)^\s*DEEPSEEK_API_KEY\s*:\s*\S+", credential_text))
        except OSError:
            credentials_mode = "unreadable"
    api_key_present = bool(__import__("os").environ.get("DEEPSEEK_API_KEY"))
    result: Dict[str, Any] = {
        "executable": executable,
        "installed": executable is not None,
        "profile": profile,
        "profile_dir": str(profile_dir),
        "profile_exists": profile_dir.is_dir(),
        "credentials_file_present": credentials_file.is_file(),
        "credentials_file_mode": credentials_mode,
        "credentials_route_hint": credentials_route_hint,
        "credentials_key_ref_present": credentials_key_ref_present,
        "api_key_env_present": api_key_present,
        "status": "ready" if executable and profile_dir.is_dir() and (api_key_present or credentials_route_hint or credentials_key_ref_present) else "needs_configuration",
    }
    if executable:
        try:
            completed = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=False)
            result["version"] = (completed.stdout or completed.stderr).strip()
        except Exception as exc:
            result["version_error"] = type(exc).__name__
    if result["status"] != "ready":
        result["next_steps"] = [
            "set DEEPSEEK_API_KEY for the DSH launch process or configure the DSH credentials service",
            "run agent-sidecar harness doctor again",
            "run scripts/run_dsh_federated_smoke.py only after status=ready",
        ]
    return result
