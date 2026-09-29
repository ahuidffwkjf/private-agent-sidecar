# Private Agent Sidecar Engineering Rules

This repository is being developed as a federated-learning platform over an encrypted Agent Sidecar. Before changing code, read `CONTINUOUS_GOAL.md`, `docs/FL_SPEC.md`, `docs/BENCHMARK_SPEC.md` and `docs/HARNESS_SPEC.md` as applicable.

## Required workflow

1. Run the smallest relevant test before editing and the full suite after editing:

   ```bash
   .venv/bin/python -m unittest discover -s tests -v
   ```

   If no project virtual environment exists, use the configured workspace Python runtime.
2. For FL changes, run both the deterministic demo and, when the transport/runtime is affected, the independent-process demo:

   ```bash
   python scripts/run_federated_demo.py
   python scripts/run_federated_process_demo.py
   ```
3. For aggregation, security or topology changes, generate a small Benchmark report and preserve its configuration and summary.
4. Do not claim a round is secure unless identity, trust, participant-set hash, base model hash, update hash, replay and timeout behavior are tested.
5. Do not claim real DeepSeek training is complete without a real DSH credential, model update artifact and successful structured train/evaluate result.

## Security invariants

- New peers are `pending` and are never trusted transitively.
- Relay never needs plaintext model updates.
- Secure FedAvg requires explicit participant trust and a committed participant set.
- Incomplete secure recovery aborts the round; it must not aggregate a partial masked sum.
- API keys, Relay tokens and credentials must never be committed or printed.
- TLS is required for cross-machine production deployment; access tokens and rate limits are defense-in-depth, not a replacement for TLS.

## Cross-Harness invariants

The FL core depends on `HarnessAdapter`, not DeepSeek-specific APIs. Toy, HTTP, Command, Direct DSH Headless and DSH Controller adapters must preserve the same structured train/evaluate/update contract. Adapters must fail closed on malformed or unstructured model results.

## Delivery checklist

Before handing off work, record:

- files changed;
- tests and commands run;
- Benchmark output directory and result summary;
- current external blockers;
- the next single highest-priority task.
