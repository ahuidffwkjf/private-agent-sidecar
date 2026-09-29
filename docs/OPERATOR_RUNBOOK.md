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
