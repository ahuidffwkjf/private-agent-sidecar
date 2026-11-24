# Private Agent Sidecar Engineering Rules

This repository develops the encrypted communication and collaboration Sidecar. Federated learning is parked in the optional `packages/federated` distribution and is not a default development goal. Before changing code, read `CONTINUOUS_GOAL.md` and the relevant specifications. For explicit FL work, read `packages/federated/README.md` and its FL, Benchmark and Harness specifications.

## Required workflow

1. Run the smallest relevant test before editing and the full suite after editing:

   ```bash
   .venv/bin/python -m unittest discover -s tests -v
   ```

   If no project virtual environment exists, use the configured workspace Python runtime.
2. For FL changes, install the optional package, run `.venv/bin/python -m unittest discover -s packages/federated/tests -v`, and run both the deterministic demo and, when the transport/runtime is affected, the independent-process demo:

   ```bash
   python scripts/run_federated_demo.py
   python scripts/run_federated_process_demo.py
   ```
3. For aggregation, security or topology changes, generate a small Benchmark report and preserve its configuration and summary.
4. Do not claim a round is secure unless identity, trust, participant-set hash, base model hash, update hash, replay and timeout behavior are tested.
5. Do not claim real DeepSeek training is complete without a real DSH credential, model update artifact and successful structured train/evaluate result.

## Security invariants

- ECS online Toy mode explicitly uses signed, end-to-end encrypted Relay delivery;
  never silently enable this transport for existing direct-only Sidecars.
- Public desktop installers must not contain ECS access tokens, SSH passwords,
  private keys or pre-consented identities. Provision access separately.
- The active toy workflow has no reward or decision-model prerequisites; preserve legacy bounty data separately.
- Toy private/group content collection requires explicit experiment consent; standard mode must not upload it.
- Research ingestion is node-authenticated; query/export/deletion require separate administrator authorization.
- Never present demo summaries as real Harness/model outputs, or execute task text while summarizing it.
- Business relay contracts must have explicit bilateral consent; hierarchy never grants transitive node trust.
- Reserve pending offers against the parent's reward budget; do not reuse accepted obligations after revocation.
- Decision models only recommend actions; they cannot bypass protocol, trust, budget or timeout checks.
- Public bounty rewards are promises only; never claim escrow, settlement or actual payment without a payment implementation.
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
