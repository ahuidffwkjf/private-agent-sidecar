# Operator Runbook

This runbook is the shortest reproducible path from a clean checkout to a local toy federation, then to a real DSH-backed node when credentials are available.

## 1 Install and preflight

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m agent_sidecar preflight \
  --data-dir node-a \
  --harness-config configs/harness/toy.example.json \
  --relay http://127.0.0.1:8787
```

Preflight also checks the selected project-key backend before a node starts:

```bash
.venv/bin/python -m agent_sidecar preflight \
  --project-key-backend external \
  --project-key-command /usr/local/libexec/sidecar-key-helper
```

It reports only backend availability and configuration status. It never includes key material. An unavailable external helper produces `needs_configuration`; Sidecar does not silently downgrade to SQLite.

For a production deployment, preflight can enforce the cross-machine baseline:

```bash
.venv/bin/python -m agent_sidecar preflight \
  --relay https://relay.example:8787 \
  --direct-endpoint https://node-a.example:8801 \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem \
  --project-key-backend keychain \
  --production
```

Production preflight fails closed unless Relay and direct endpoint URLs use HTTPS, the TLS certificate/key pair exists, and project key storage is not SQLite.

For a real DSH machine, check without printing secrets:

```bash
.venv/bin/agent-sidecar harness doctor --dsh-bin dsh --profile headless
```

The doctor reports whether the executable, profile and credential reference are present. It never prints an API key.

## 2 Local toy federation

Start Relay:

```bash
.venv/bin/agent-sidecar relay \
  --host 127.0.0.1 \
  --port 8787 \
  --database runs/relay.db
```

Start the encrypted process demo:

```bash
.venv/bin/python scripts/run_federated_process_demo.py
```

Run the deterministic benchmark:

```bash
.venv/bin/agent-sidecar benchmark \
  --config configs/benchmark/small.json \
  --output runs/benchmark-small
```

Review:

```text
runs/benchmark-small/comparison.md
runs/benchmark-small/comparison.csv
runs/benchmark-small/matrix.json
```

## 3 Trust UI

Start one Sidecar:

```bash
.venv/bin/agent-sidecar serve \
  --host 127.0.0.1 \
  --port 8801 \
  --data-dir runs/node-a \
  --relay http://127.0.0.1:8787
```

Open:

```text
http://127.0.0.1:8801/ui/trust
```

New peers are `pending`. A peer is usable only after the local operator checks it and saves the allowlist. Trust is not transitive.

## 4 Cross-machine production baseline

For Tailcat v0.7.0 port forwarding, use [TAILCAT_DEPLOYMENT.md](TAILCAT_DEPLOYMENT.md)
and the templates in `deploy/tailcat/`. This checkout has no WebSocket Relay;
the recovered legacy deployment's port 8788 is intentionally not exposed.
TLS and explicit peer trust remain required inside the tunnel.

Use TLS and a Relay access token:

```bash
export RELAY_ACCESS_TOKEN='store-this-outside-the-repository'

.venv/bin/agent-sidecar relay \
  --host 0.0.0.0 \
  --port 8787 \
  --database /var/lib/sidecar/relay.db \
  --access-token "$RELAY_ACCESS_TOKEN" \
  --rate-limit 120 \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem
```

For project epoch content keys, use the OS keychain in production when available:

```bash
.venv/bin/agent-sidecar serve \
  --data-dir /var/lib/sidecar/node-a \
  --relay https://relay.example:8787 \
  --project-key-backend keychain
```

`sqlite` remains the development default. `file` stores key material in a 0600 local file and is a migration/testing fallback, not a production HSM substitute. On macOS, `keychain` uses the `security` command and never prints key material. Existing SQLite project keys are migrated into the selected external backend on startup and removed from the SQLite key-material column after successful migration.

For an operator-managed TPM/HSM/KMS helper, use the external JSON stdin/stdout adapter:

```bash
.venv/bin/agent-sidecar serve \
  --data-dir /var/lib/sidecar/node-a \
  --relay https://relay.example:8787 \
  --project-key-backend external \
  --project-key-command /usr/local/libexec/sidecar-key-helper
```

The helper receives `save`, `get` and `delete` JSON operations on stdin and returns JSON on stdout. Key material is never placed in command-line arguments. The helper process must be operator-owned, authenticated to the device-backed keystore, and configured not to log stdin/stdout.

The exact protocol and fail-closed rules are documented in `docs/interfaces/project-key-helper.md`.

For the real multi-host acceptance procedure, use [`docs/CROSS_MACHINE_DEPLOYMENT_CHECKLIST.md`](CROSS_MACHINE_DEPLOYMENT_CHECKLIST.md). Local loopback demos do not prove NAT, firewall, TLS trust or cross-host Gossip behavior.

Node:

```bash
.venv/bin/agent-sidecar serve \
  --host 0.0.0.0 \
  --port 8801 \
  --data-dir /var/lib/sidecar/node-a \
  --relay https://relay.example:8787 \
  --relay-token "$RELAY_ACCESS_TOKEN" \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem
```

Use a trusted CA or an internal CA distributed to every client. Do not disable certificate verification.

## 5 Real DSH preflight and smoke test

Configure credentials through the DSH credentials service or the owner-only `~/.dsh/.credentials.yaml` `DEEPSEEK_API_KEY` reference. Then run:

```bash
.venv/bin/agent-sidecar harness doctor --dsh-bin dsh --profile headless
.venv/bin/python scripts/run_dsh_federated_smoke.py
```

The smoke test requires JSON structured output containing `model`, `num_examples` and optional `metrics`. Natural-language output is rejected.

## 6 Federated audit

Read local round and recovery audit data:

```bash
curl -s http://127.0.0.1:8801/v1/federated/rounds
curl -s http://127.0.0.1:8801/v1/federated/recoveries
```

For Secure FedAvg, configure every participant to trust every other participant. Use `secure_max_dropouts` only when the operator accepts the recovery policy; failed recovery aborts the round.

## 7 Incident handling

- `invalid_relay_token`: check the token source and rotate it outside the repository.
- `stale_result_lease`: discard the result and inspect Coordinator restart/lease state.
- `secure aggregation recovery failed`: keep the round aborted; do not manually mark it completed.
- `MISSING_CREDENTIAL`: configure DSH credentials; do not place secrets in configs or logs.
- `model hash mismatch` or `update hash mismatch`: quarantine the node and inspect its key/trust state.

## 8 Direct project/private channels

For local testing, start each Sidecar with a reachable direct endpoint. The endpoint is announced in the signed `node.capabilities` broadcast:

```bash
.venv/bin/agent-sidecar serve \
  --host 127.0.0.1 \
  --port 8801 \
  --direct-endpoint http://127.0.0.1:8801 \
  --data-dir runs/node-a \
  --relay http://127.0.0.1:8787
```

For production, use an HTTPS endpoint and the same TLS certificate used by the Sidecar:

```bash
.venv/bin/agent-sidecar serve \
  --host 0.0.0.0 \
  --port 8801 \
  --direct-endpoint https://node-a.example:8801 \
  --data-dir /var/lib/sidecar/node-a \
  --relay https://relay.example:8787 \
  --relay-token "$RELAY_ACCESS_TOKEN" \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem
```

The Dashboard communication selector routes Public through Relay, writes project discussion to the replicated project board, and routes private messages directly. Board synchronization and private delivery never fall back to Relay. Project Owners issue signed membership manifests when granting project trust; accepting the project enables project-member auto-trust by default without changing global trust. Every project member stores a local board replica, so the Owner may be offline while other members continue to read and post. Use the board's manual sync action after a disconnected member returns; the running Sidecar also performs periodic synchronization with its capability heartbeat.

Direct HTTPS uses the certificate fingerprint advertised in the signed capability broadcast. The sender pins that fingerprint and fails closed on mismatch; HTTP direct endpoints are for local tests only. The regression suite includes a real TLS-wrapped Sidecar direct-message exchange.

Use the Project invitation panel instead of directly granting project trust. The Owner first sends an encrypted signed invitation. The candidate is not a member until it selects Accept and the Owner later polls and verifies the signed response. Rejection leaves the member manifest unchanged.

### Independent project-board process acceptance

Run the project collaboration process demo:

```bash
.venv/bin/python scripts/run_project_board_process_demo.py
```

It starts Relay and three independent Sidecar OS processes, creates and accepts project invitations, sets partial Gossip fanout, stops the Owner, verifies members continue posting and synchronizing, restarts the Owner, performs Merkle recovery, rotates membership/content keys by removing a member, and verifies the removed member is denied board access.

### Independent process partition and recovery acceptance

Run the partition/recovery demo:

```bash
.venv/bin/python scripts/run_project_board_partition_demo.py
```

It verifies a local board commit while Relay is offline, stops two nodes to create two sequentially isolated partitions, publishes independent events, restarts all nodes, and runs fanout-1 Merkle Gossip until every node has the same event-set root.

After convergence it runs three additional sync cycles and asserts that roots remain stable and each local event ID is unique, providing a bounded duplicate/oscillation check.

### Disposable production preflight demonstration

Run the local production-gate demonstration:

```bash
.venv/bin/python scripts/run_production_preflight_demo.py
```

It generates a temporary TLS pair and executable external-key helper, runs `preflight --production`, prints only configuration status, and deletes all temporary material on exit. It does not prove remote NAT/firewall reachability or real hardware-backed key storage.
