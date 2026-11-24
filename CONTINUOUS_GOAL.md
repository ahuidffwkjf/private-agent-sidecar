# Continuous Goal Execution

## Current Scope (Toy Collaboration)

The main project is the encrypted communication and collaboration Sidecar.
Federated learning is parked in `packages/federated` as an optional distribution;
do not automatically resume FL development or require training credentials for
ordinary communication work. Historical goals and results are preserved in that
package's `docs/` directory.

The active priority is the no-reward toy collaboration loop: publisher Harness,
task summary, WeChat-style task hall, explicit applications/approval, independent
hierarchical project groups and opt-in experimental content traces. See
`docs/TOY_MODE.md`. Legacy reward bounties and decision heads remain compatible
but are not prerequisites or active training work. Task hierarchy never grants
transitive node trust. The toy task directory is a trusted metadata coordinator;
chat transport and the experimental research collector are separate.

## Workflow

1. Run the smallest relevant test before edits and the full core suite afterward:
   `.venv/bin/python -m unittest discover -s tests -v`.
2. Preserve identity, explicit trust, encrypted transport and project permissions.
3. For explicit FL work, install the optional package and follow its README,
   FL_SPEC, HARNESS_SPEC and BENCHMARK_SPEC; run its suite and both FL demos.
4. Record changed files, validation commands, benchmark applicability, blockers
   and the next single highest-priority task.

## Next Priority

Complete the ECS-hosted Toy rollout: one independent local identity per desktop,
ECS as the default shared Relay and task directory, explicit encrypted Relay
delivery, online discovery and consented invitations. OrangePi is no longer the
active deployment target; keep its inaccessible historical database untouched.
Public port reachability, HTTPS trust provisioning and real multi-user acceptance
are release evidence, not assumptions. Never embed access tokens in public apps.
Keep the four-user local demo optional. Real DSH summaries remain a later,
credential-dependent acceptance task; do not resume rewards or model training.
