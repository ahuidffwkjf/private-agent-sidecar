# Cross Harness Adapter Specification

## Python protocol

A Harness adapter implements `describe`, `load_global`, `train`, `evaluate`, `export_update` and `import_global`. The FL coordinator and node use this protocol rather than DeepSeek-specific functions.

## HTTP protocol

- `GET /v1/harness/describe`
- `POST /v1/harness/train`
- `POST /v1/harness/evaluate`
- `POST /v1/harness/import-global`
- `GET /healthz`

`HttpHarnessAdapter` is the client and `agent-sidecar harness serve` is the reference server. `agent-sidecar harness describe --config <path>` loads a local adapter configuration and prints its capability descriptor.

## Command adapter

`CommandHarnessAdapter` renders `{input}` and `{output}` placeholders into a command. The command reads a JSON request and must create a JSON response. This supports existing harnesses without importing them into the Sidecar process.

## DeepSeek

`DeepSeekHarnessAdapter` is command-based and advertises instruction-tuning, LoRA and safetensors capabilities. The repository does not contain the external DeepSeek training entry point, so deployments must provide commands and model paths in local configuration. Core tests never download a model or require PyTorch.


## Existing DeepSeek Harness Controller

The repository also contains an existing DSH + MicroVM controller with `POST /tasks`, `GET /tasks/<job_id>`, registry tokens, polling DSH Agents and MicroVM lifecycle management. `DshControllerAdapter` wraps that public controller API without importing DSH internals:

```json
{
  "kind": "dsh-controller",
  "controller_url": "http://127.0.0.1:8081",
  "registry_token": "..."
}
```

The adapter is a task-execution Harness, not a federated model-training adapter. It can be used for orchestration, evaluation jobs and future cross-Harness control-plane integration. Model training still uses the `HarnessAdapter` train/evaluate contract.


## Structured DSH federated training bridge

For a DSH Harness that can run training code inside its MicroVM, use `kind: dsh-training`. The DSH task must return JSON with this contract:

Training result:

```json
{
  "model": {"weights": [0.1], "bias": [0.0]},
  "num_examples": 100,
  "metrics": {"loss": 0.4, "accuracy": 0.8},
  "train_ms": 123
}
```

Evaluation result:

```json
{"metrics": {"loss": 0.4, "accuracy": 0.8}, "eval_ms": 20}
```

The `DshControllerTrainingAdapter` sends a structured operation request through the existing `/tasks` API, polls `/tasks/<job_id>`, parses the returned JSON and fails closed if the Harness returns unstructured natural language. This makes DSH usable as a federated node without coupling the coordinator to DSH internals.


## Direct local DSH Headless adapter

When `dsh` is installed locally, use `kind: dsh-headless` with `configs/harness/dsh-headless.example.json`. The adapter runs `dsh --profile headless`, sets `DSH_PERMISSION_MODE` and `DSH_TELEMETRY_MODE`, parses JSON-only output and fails closed on natural-language output. A real run also requires DSH credentials, for example `DEEPSEEK_API_KEY` or a credential stored by the DSH credentials service.


## Real DSH smoke test

Run `python scripts/run_dsh_federated_smoke.py` after credentials are configured. The script performs a non-secret preflight first and exits before invoking DSH when the local executable, profile or credential route is unavailable.
