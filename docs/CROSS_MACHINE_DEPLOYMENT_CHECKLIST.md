# Cross-Machine Deployment Acceptance Checklist

This checklist is intentionally separate from the local process demos. A local loopback result does not prove that NAT, firewall, DNS, TLS trust or cross-machine direct routing works.

## Required topology

Use at least two independently administered hosts:

```text
Host A: Relay + Sidecar A
Host B: Sidecar B
```

For a meaningful Gossip test, use a third host or a second network segment:

```text
Host A: Relay + Sidecar A
Host B: Sidecar B
Host C: Sidecar C
```

The hosts must not share the same process namespace or the same local loopback interface. Record the exact OS, address family, NAT mode, firewall rules and DNS names in the acceptance artifact.

## 1. TLS and identity setup

On every Relay/Sidecar host:

- install an operator-managed certificate and private key;
- ensure the certificate SAN covers the DNS name used by peers;
- keep private keys outside the repository with restrictive permissions;
- configure Relay and every direct Sidecar endpoint with TLS;
- calculate and record each direct certificate SHA-256 fingerprint;
- publish only the certificate fingerprint in the signed capability broadcast;
- verify that no private key, Relay token or API key appears in the broadcast.

Run the production gate on every node:

```bash
.venv/bin/python -m agent_sidecar preflight \
  --relay https://relay.example:8787 \
  --direct-endpoint https://node-a.example:8801 \
  --tls-cert /etc/sidecar/tls/fullchain.pem \
  --tls-key /etc/sidecar/tls/privkey.pem \
  --project-key-backend external \
  --project-key-command /usr/local/libexec/sidecar-key-helper \
  --production
```

Required result:

```text
status = ready
relay_url_https = true
direct_endpoint_https = true
tls_pair_valid = true
project_key_backend_non_sqlite = true
project_key_backend_available = true
```

## 2. Firewall and NAT matrix

Test each path independently and record the result:

| Path | Expected | Evidence |
|---|---|---|
| Host A -> Relay HTTPS | reachable | `curl`/health check output |
| Host B -> Relay HTTPS | reachable | `curl`/health check output |
| Host A -> Host B direct HTTPS | reachable or documented NAT limitation | `gossip-peers?probe=true` |
| Host B -> Host A direct HTTPS | reachable or documented NAT limitation | `gossip-peers?probe=true` |
| Host C -> Host B direct HTTPS | reachable or documented NAT limitation | direct health probe |
| Relay -> no project content path | not required | Relay database contains no board event body |

If direct connectivity is impossible because of NAT, the deployment must provide an operator-approved secure path. Do not silently route project content through Relay.

## 3. Trust and project acceptance

Record evidence for:

1. New node starts `pending`.
2. Relay authentication does not create peer trust.
3. A sends a signed trust request to B.
4. B accepts and creates reciprocal trust.
5. A and B show `mutual`.
6. Owner sends a signed project invitation.
7. Invitee is not a member before acceptance.
8. Invitee accepts.
9. Owner processes the signed acceptance and publishes the new membership manifest.
10. Project trust does not enter the global trust allowlist.

## 4. Direct board and Relay outage

1. Create a project with at least three members.
2. Set `gossip_fanout=1`.
3. Stop or firewall Relay after membership bootstrap.
4. Publish a board event from a non-Owner member.
5. Confirm the event is committed locally.
6. Confirm direct member synchronization still works where firewall rules permit.
7. Confirm no Relay fallback occurs.
8. Restart Relay and verify control-plane recovery only.

The Relay database must not contain project board plaintext or the decrypted event payload.

## 5. Network partition and recovery

Create two network partitions that cannot directly reach one another, while preserving each partition's local Sidecar availability:

```text
Partition 1: A, B
Partition 2: C
```

During partition:

- A/B publish one or more signed encrypted events;
- C publishes independent signed encrypted events;
- no event is routed through Relay as a project-content fallback;
- each partition can read its local replica;
- event IDs remain unique.

After healing the network:

1. Run `POST /v1/projects/<project_id>/board/sync/merkle` repeatedly.
2. Confirm all nodes converge to one `event_set_root`.
3. Confirm every expected event exists exactly once.
4. Continue for at least three additional cycles.
5. Confirm the root remains stable and no duplicate events appear.
6. Record the maximum convergence time and number of failed attempts.

## 6. Member removal and key isolation

1. Remove C through the Owner API.
2. Confirm the new membership epoch is higher.
3. Confirm the content key fingerprint changed.
4. Confirm C receives a revocation manifest but no new content-key envelope.
5. Publish a new board event.
6. Confirm A/B can decrypt it.
7. Confirm C cannot read the new event or submit a valid new-epoch event.
8. Confirm historical events are only readable when the node legitimately holds the historical epoch key.

## 7. Required acceptance artifacts

Preserve these files outside source control if they contain sensitive addresses or fingerprints, or redact them before commit:

```text
cross-machine-topology.json
production-preflight-node-a.json
production-preflight-node-b.json
tls-fingerprint-record.txt
firewall-nat-matrix.json
trust-and-invitation-log.json
board-convergence-summary.json
key-rotation-summary.json
relay-content-audit.json
```

Do not commit:

- private keys;
- Relay tokens;
- API keys;
- project content keys;
- helper credentials;
- unredacted internal network secrets.

## Acceptance rule

Do not claim production-ready cross-machine collaboration until all sections have evidence. A local loopback demo, a green unit suite or a successful production preflight alone is insufficient to prove NAT, firewall, TLS trust and multi-host Gossip behavior.

## 2026-10-14 partial evidence

The provided remote host `189.24.102.37` was used for a controlled one-way acceptance:

- remote Relay control endpoint: HTTP port `18787`;
- remote Sidecar direct endpoint: HTTPS port `18801`;
- certificate fingerprint pinning: verified;
- local-to-remote encrypted direct message: delivered;
- remote SQLite metadata confirmed message type and sender ID without outputting the message body;
- no Relay fallback was used for the direct message;
- temporary source, certificate, database and runtime directories were removed from the remote host after the test.

This is not full production acceptance because the Relay control plane in this isolated test used HTTP, only one direct direction was publicly reachable, and a second independent host/firewall segment was not available.
