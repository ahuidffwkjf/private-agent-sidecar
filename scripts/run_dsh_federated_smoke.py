"""Run one real DSH structured federated-training smoke request.

This command never prints credentials. It fails before invoking DSH when the
local doctor reports that the executable/profile/credential route is missing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_sidecar.federated.types import TrainRequest
from agent_sidecar.harness import DshHeadlessAdapter, diagnose_dsh


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a real DSH structured FL task")
    parser.add_argument("--dsh-bin", default="dsh")
    parser.add_argument("--profile", default="headless")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    doctor = diagnose_dsh(args.dsh_bin, args.profile)
    print(json.dumps(doctor, ensure_ascii=False, indent=2))
    if doctor["status"] != "ready":
        print("DSH preflight is not ready; configure credentials before invoking the Harness.", file=sys.stderr)
        return 2
    adapter = DshHeadlessAdapter(args.dsh_bin, args.profile, args.timeout)
    request = TrainRequest(
        round_id="dsh-smoke-round",
        model_id="structured-smoke-model",
        model_hash="sha256:smoke-input",
        global_model={"weights": [0.0], "bias": [0.0]},
        local_steps=1,
        learning_rate=0.1,
    )
    result = adapter.train(request)
    print(json.dumps({"num_examples": result.num_examples, "metrics": result.metrics, "model": result.model, "train_ms": result.train_ms}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
