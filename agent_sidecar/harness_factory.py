from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .harness import CommandHarnessAdapter, DeepSeekHarnessAdapter, DshControllerAdapter, DshControllerTrainingAdapter, DshHeadlessAdapter, HttpHarnessAdapter, ToyHarness
from .federated.simulator import _dataset


def load_harness(config_path: Path):
    config: Dict[str, Any] = json.loads(config_path.read_text("utf-8"))
    kind = config.get("kind", config.get("name", "toy")).lower()
    if kind == "toy":
        return ToyHarness(_dataset(int(config.get("seed", 7)), int(config.get("samples", 40)), int(config.get("node_index", 0)), bool(config.get("non_iid", False))))
    if kind in ("http", "remote"):
        return HttpHarnessAdapter(config["base_url"], float(config.get("timeout", 300)))
    if kind in ("dsh", "deepseek-controller", "dsh-controller"):
        return DshControllerAdapter(config["controller_url"], config["registry_token"], float(config.get("timeout", 900)), float(config.get("poll_interval", 1)))
    if kind in ("dsh-training", "deepseek-controller-training"):
        return DshControllerTrainingAdapter(config["controller_url"], config["registry_token"], float(config.get("timeout", 900)), float(config.get("poll_interval", 1)), config.get("task_capabilities"), config.get("task_tools"))
    if kind in ("dsh-headless", "deepseek-headless"):
        return DshHeadlessAdapter(config.get("dsh_bin", "dsh"), config.get("profile", "headless"), float(config.get("timeout", 900)), config.get("permission_mode", "workspace-write"), config.get("telemetry_mode", "DISABLED"))
    train_command = list(config.get("train_command", []))
    evaluate_command = config.get("evaluate_command")
    if not train_command:
        raise ValueError("command/deepseek harness config requires train_command")
    cls = DeepSeekHarnessAdapter if kind == "deepseek" else CommandHarnessAdapter
    return cls(train_command, list(evaluate_command) if evaluate_command else None, config.get("cwd"))
