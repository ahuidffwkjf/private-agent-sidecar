# Federated Learning Benchmark Specification

The small deterministic benchmark uses a two-feature binary classification task and fixed seeds. It records every round and compares FedAvg, FedProx, coordinate median, trimmed mean and Krum across three communication structures: Coordinator-Star, Hierarchical groups of two, and one clockwise Gossip pass.

The simulator uses the same deterministic aggregation result for model-quality comparison while separately modeling the data/control-plane traffic of each structure. This makes the result reproducible and prevents the benchmark from pretending to emulate a real network scheduler.

## Structures

- `star`: every participant sends its update to the Coordinator and receives the new global model from it.
- `hierarchical`: participants are grouped in pairs; group edges reduce Coordinator-level fan-in/fan-out, while the full network still includes group ingress/egress.
- `gossip`: one clockwise update pass and one model pass; the hop estimate grows with the number of participants.

The result includes both total `network_messages` and `coordinator_messages`, because hierarchical communication can reduce Coordinator pressure without reducing every physical edge transmission. Optional clipping and Gaussian noise can be enabled with `dp_clip_norm` and `dp_noise_std`. The benchmark now reports a conservative basic Gaussian RDP upper bound (`privacy_epsilon`, `privacy_delta`) under explicit replace-one/full-participation assumptions; it does not apply subsampling amplification or claim a guarantee when those assumptions are violated.

## Scenarios

- no attack
- label flipping data poisoning
- scaled model update
- sign-flipped update
- random Byzantine update

## Metrics

Security metrics include clean-reference accuracy, accuracy drop, malicious update acceptance rate, honest update rejection rate and a strategy-specific malicious influence proxy. The proxy is explicitly labeled as an experimental comparison metric, not a formal security proof.

Communication metrics include bytes sent, bytes received, total messages, average update size, estimated network bytes, topology messages and Coordinator-level messages.

Latency includes measured local wall time plus a documented topology hop estimate. Each per-round row records mean round latency, p50/p95 aggregate latency, wall latency, network latency and aggregation time.

## Output

Each scenario directory contains:

```text
config.json
results.json
per_round.csv
per_round.jsonl
summary.md
```

The matrix root contains `matrix.json`, `comparison.csv` and `comparison.md`. The default configuration runs 3 structures x 6 aggregation strategies x 5 attack scenarios = 90 runs. `comparison.md` ranks clean runs by accuracy/latency/network cost and attack runs by influence proxy/accuracy drop; it must not be interpreted as a formal security proof.
