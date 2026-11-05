# Implementation Status

Status checked on 2026-10-14.

## Completed and verified

| Requirement | Evidence |
|---|---|
| Toy federated learning end to end | `scripts/run_federated_demo.py`, `scripts/run_federated_process_demo.py` |
| Encrypted Coordinator and Node transport | `agent_sidecar/federated/runtime.py`, `tests/test_federated.py` |
| Explicit trust UI and allowlist | `/ui/trust`, `/v1/peers/candidates`, `/v1/peers/trust` |
| FedAvg and FedProx | `agent_sidecar/federated/aggregators.py`, Toy Harness tests |
| Robust aggregation | Median, Trimmed Mean and Krum tests |
| Secure FedAvg | X25519 pairwise masks and runtime integration |
| Secure dropout recovery | one and two dropped participant recovery tests |
| Round/update/recovery replay protection | persistent SQLite tables and tests |
| Coordinator restart recovery | `fl_coordinator_state`, `pending_round`, `resume_pending_round` |
| Coordinator single-owner protection | `fl_coordinator_lease` and lease test |
| Security/communication/latency benchmark | 3 topologies x 6 strategies x 5 scenarios, comparison reports |
| Privacy experiment | clipping, Gaussian noise and declared-assumption RDP upper bound |
| Cross Harness Python protocol | Toy, HTTP, Command, DeepSeek and DSH adapters |
| Existing DSH Controller integration | `/tasks` and `/tasks/<job_id>` adapter tests |
| Direct local DSH integration | `DshHeadlessAdapter`, `harness doctor`, real smoke script |
| TLS, Relay token and rate limiting | CLI flags and tests |
| Operator documentation | `docs/OPERATOR_RUNBOOK.md` |
| Unified Public/project/private routing | `agent_sidecar/service.py`, `docs/interfaces/messaging-routing.md` |
| Direct Sidecar transport and fail-closed delivery | `/v1/direct/messages`, `/v1/direct/healthz`, `tests/test_direct_routing.py` |
| Owner-signed project membership and project auto-trust | `project_manifests`, `project_members`, `project_settings`, direct membership tests |
| Global trust candidates in project UI | `/v1/projects/<project_id>/candidates`, Dashboard selector |
| Replicated project board MVP | signed `project-board/1` events, local SQLite replicas, direct member synchronization, Owner-offline test |
| Directional node trust and reciprocal confirmation | signed `node-trust/1` statements, encrypted Relay notification, accept/reject/block/revoke and mutual-state tests |
| Explicit project invitation acceptance | signed encrypted invitation/response, no membership before acceptance, reject and HTTP/UI tests |
| Historical membership proof for board events | Owner manifest history, `/membership/history`, event-embedded manifest proof, delayed old-epoch test |
| Per-epoch project content key rotation | encrypted key envelopes in Owner manifest, encrypted board content, removed-member confidentiality test |
| Configurable partial Gossip fanout | cyclic neighbor selection, `gossip-settings` API, partial-neighbor test |
| Health-aware Gossip neighbor scoring | endpoint availability, recent direct success/failure counts, advertised p95 latency and `/gossip-peers` inspection API |
| Persistent decaying Gossip metrics | per-project/member SQLite metrics, one-hour half-life, restart persistence and monotonic-update tests |
| Bounded Gossip attempt window | 100 retained attempts per member pair, 20-attempt scoring window and pruning test |
| Long-running Gossip stability check | partition demo runs three post-convergence cycles and asserts stable roots plus unique event IDs |
| Independent project-board process acceptance | `scripts/run_project_board_process_demo.py`: invitations, Owner outage/restart, Merkle recovery, removal rejection |
| Independent partition/recovery acceptance | `scripts/run_project_board_partition_demo.py`: Relay-local commit, isolated events, fanout-1 Merkle convergence |
| HTTPS direct transport pinning | TLS-wrapped direct Sidecar exchange with certificate fingerprint verification test |
| External project-key storage | file backend with 0600 migration and macOS Keychain backend with fake-command tests; HSM/TPM remains |
| Project-key preflight | backend availability checks for sqlite/file/keychain/external; missing external helper fails closed without secrets |
| Production preflight demonstration | temporary TLS pair, external helper readiness and cleanup script |
| Cross-machine deployment checklist | TLS, firewall/NAT, trust, outage, partition, convergence and key-isolation acceptance procedure |
| Partial real cross-machine evidence | `runs/cross-machine-acceptance-20261014/summary.json`: local-to-remote HTTPS fingerprint-pinned encrypted direct delivery; full bidirectional/NAT acceptance remains |
| External helper hardening | strict stdin/stdout protocol, scope validation, helper failure fail-closed tests and protocol documentation |
| WeChat full-function Skill catalog | 361 independent manifests across 13 product modules, searchable Dashboard, individual/bulk install APIs and MCP catalog tools |

## Current verified test gate

```text
The suite now contains 91 tests. The last unrestricted full run before the latest Gossip metric additions passed 81 tests; the non-network key-store/preflight/helper/gossip-metric tests pass 9/9; the new direct health-probe test requires loopback permission. A subsequent sandbox-only full attempt could not bind loopback ports for network tests; rerun the full suite with loopback permission before delivery.
OK
```

Primary command:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## External prerequisites still required

These are not simulated as complete:

1. A valid DeepSeek credential for the local DSH `deepseek-official` route.
2. A real DSH task that returns LoRA/Safetensors model updates in the structured contract.
3. A real model-level federated round using those artifacts.
4. Production-grade key rotation and threshold-verified recovery.
5. Distributed Relay rate limiting and production secret management.

The current machine has DSH `0.1.5-rc.3` and a headless profile, but `harness doctor` reports `needs_configuration` because no usable DeepSeek credential route is configured.

## Do not claim yet

Do not claim real DeepSeek model training, formal end-to-end differential privacy, production secure aggregation proof, or distributed production deployment until the external prerequisites and corresponding runtime evidence exist.
