# Federated Learning Runtime Specification

## Runtime roles

`FederatedCoordinator` owns the global model, creates signed and encrypted `fl.round.request` messages, validates returned round/model identifiers, runs an aggregation strategy and broadcasts `fl.round.result`.

`FederatedNode` consumes only messages from a locally trusted coordinator, checks the round model hash, invokes a `HarnessAdapter`, and returns one `fl.update.submit` for each round. Processed round identifiers are retained in memory to prevent duplicate work; transport-level message identifiers are persisted by the Sidecar store.

## Trust

A node starts with no trusted peers. `GET /v1/peers/candidates` lists Relay identities as `pending`, `PUT /v1/peers/trust` replaces the global local allowlist, and `DELETE /v1/peers/trust/<agent_id>` revokes a global peer. Project FL should instead use project-scoped temporary trust. Project grants are valid only while the project is active and are revoked when the project closes. A changed signing key is rejected and requires an explicit rotation flow.

## Message types

- `fl.round.request`
- `fl.update.submit`
- `fl.round.result`

Every round request contains a `RoundConfig`, global model, model hash, nonce, minimum client count, deadline and a participant-set hash. Nodes reject a config whose participant list does not match the commitment. Secure rounds therefore cannot silently change the mask participant set. Encrypted Sidecar envelopes provide sender authentication, TTL, replay-resistant message identifiers and confidentiality from the Relay.

## Current limits

The core model representation is a named JSON float-vector map. This keeps CI independent of NumPy and PyTorch. `agent_sidecar.federated.artifacts` provides fixed-size chunking with per-chunk and whole-artifact SHA-256 verification for larger payloads. Optional update clipping and Gaussian noise are available in the deterministic simulator for privacy/utility experiments. They are not a formal differential-privacy guarantee without a privacy accountant. Secure FedAvg is implemented in the simulator and Sidecar runtime with pairwise masks and bounded dropout recovery. Production deployments still require authenticated ephemeral key rotation, stronger recovery thresholds and TLS around Relay HTTP.


## Secure FedAvg simulation

`secure_fedavg` is available both in the deterministic simulator and in the Sidecar runtime. Each node contributes a weighted masked update; pairwise masks are derived from authenticated X25519 identities, cancel only in the complete participant sum, and the Coordinator divides the sum by total examples. The Coordinator cannot interpret an individual masked update. Secure participants must explicitly trust every other participant so pairwise public identities are available; the Coordinator itself does not receive pairwise secrets. If any expected participant drops out, the runtime can be configured with `secure_max_dropouts > 0`: surviving nodes receive a recovery request and reveal only the pairwise mask contribution associated with the missing participant. The Coordinator removes those contributions and aggregates the surviving weighted updates. Each recovery response carries a persistent `recovery_id`; duplicate recovery submissions are rejected and stored in `fl_recoveries` for audit. If recovery is disabled, incomplete, or fails validation, the round is persisted as `aborted` and no partial masked sum is aggregated. Production key rotation and stronger dropout thresholds remain required extensions.


## Privacy accounting

The simulator exposes `gaussian_rdp_accountant`, a conservative composition bound for clipped full-participation Gaussian updates. It reports epsilon/delta only under replace-one adjacency, sensitivity `2 * clip_norm`, independent Gaussian noise, and no subsampling amplification. Real deployments must replace or validate this accountant against their sampling, clipping and optimizer semantics.


## Audit endpoints

The Sidecar exposes read-only local audit endpoints:

- `GET /v1/federated/rounds`
- `GET /v1/federated/recoveries`

They expose round status and recovery metadata from SQLite without returning model plaintext or pairwise secrets.


## Transport TLS

Relay and Sidecar accept `--tls-cert` and `--tls-key`. TLS is optional for local tests but required for cross-machine production deployment. Certificate validation remains the responsibility of the client trust store; the implementation does not disable verification for convenience.


## Relay access authentication

Relay accepts an optional `--access-token`. When configured, registration, identity directory, message enqueue/inbox and ACK endpoints require `X-Relay-Token`. Sidecars pass the same secret with `--relay-token`. Health remains public for liveness checks; transport TLS should be enabled for cross-machine deployments.


## Relay rate limiting

Relay supports an optional in-memory source-IP rate limit via `--rate-limit N`. Protected requests beyond N per 60-second window return HTTP 429 with a retry hint. This is a local guardrail, not a distributed quota system; production deployments should also enforce limits at the TLS proxy or gateway.


## Coordinator restart recovery

The Coordinator persists round configuration in `fl_rounds.config_json` and global model state in `fl_coordinator_state`. A new Coordinator instance can call `pending_round()` and `resume_pending_round()` to continue a claimed/submitted round from Relay-delivered updates instead of silently starting over. Invalid or missing model hashes are not resumed.
