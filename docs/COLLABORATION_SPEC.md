# Node Collaboration Target Specification

This document records the current product target for Sidecar discovery, trust, projects and project discussion. The detailed wire contracts live in `BROADCAST_SPEC.md`, `PROJECT_TRUST_SPEC.md` and `PROJECT_BOARD_SPEC.md`.

## 1. Node login and discovery

After successful login, a node and Relay authenticate each other for control-plane access. That relationship is not transitive peer trust. Every non-Relay node automatically collects a privacy-safe hardware, peripheral, network and Harness summary, signs a fixed-format `node.capabilities` broadcast and publishes it through Relay. Relay verifies and broadcasts it to all nodes. All non-Relay dashboards expose the discovery list.

Capability broadcasts contain the Agent ID and signing public key/fingerprint, never the private key, API keys, Relay tokens, credentials, local file contents, camera frames or microphone recordings.

## 2. Long-term node trust

A newly discovered peer remains `pending`. One node may create a signed one-way trust statement. The target receives a notification and may accept, reject or block the reciprocal relationship. Only two matching decisions produce long-term mutual trust. Relay trust and third-party trust never transitively create peer trust. Key changes require explicit rotation/reverification.

## 3. Project invitation and temporary trust

Every non-Relay node can create a project. A creator may invite a mutually trusted peer or an otherwise unknown discovered peer. Unknown-peer invitations explicitly request project-scoped temporary trust only. The target must actively accept before becoming a member.

After acceptance, the Owner issues a monotonically increasing signed membership manifest. Every current member receives project-scoped trust for that signed participant set. It never modifies global trust. Leaving, removal or project closure revokes project access and advances the membership epoch.

## 4. Replicated project board

Each project has one logical board rather than member-to-member project chats. Every current member stores a local signed-event replica. Posts, replies, plans, tasks and decisions are visible to all project members after synchronization. Project outsiders have no read or write access.

The board is not hosted by the Owner. Any member can read locally available content and create a locally committed event while the Owner or Relay is offline. Direct encrypted member synchronization eventually distributes the event. Persistent full-mesh connections are not required. Relay never stores, forwards or decrypts board content.

## 5. Required security checks

- identity and key proof validation;
- signed capability broadcasts and board events;
- explicit project participant-set commitment;
- message/event hash validation;
- duplicate and replay rejection;
- timeout/expiry validation;
- project membership epoch enforcement;
- immediate rejection of removed members for new operations;
- TLS and certificate pinning for cross-machine direct transport.

## 6. Current implementation status

| Area | Status | Evidence / remaining work |
|---|---|---|
| Signed automatic capability broadcast | Implemented | `node.capabilities`, capability APIs and dashboard discovery |
| Pending/global local trust allowlist | Implemented | trust candidates, pinned identities, revoke flow |
| Signed one-way trust notification and reciprocal acceptance | Implemented | encrypted Relay control message, independent signature, accept/reject/block/revoke, mutual-state tests |
| Owner-signed project membership | Implemented | epoch manifest and participant-set hash |
| Explicit invitation/accept/reject workflow | Implemented | encrypted signed Owner invitation and invitee response; no membership before acceptance |
| Project-scoped member trust | Implemented, default after local project acceptance | never enters global allowlist |
| Replicated board local event log | Implemented MVP | signed events, SQLite replicas, direct sync, offline Owner test |
| Efficient partial-overlay reconciliation | Implemented MVP | encrypted bucket-range Merkle request/response, configurable fanout, persisted/decaying health-aware scoring, 100-attempt bounded window, recent-failure cooldown, bounded gossip TTL/deduplication, stable-cycle duplicate check and partition convergence demo |
| Historical membership proof and epoch key rotation | Implemented MVP | historical Owner manifests, event-embedded proofs, per-epoch encrypted content keys, removed-member confidentiality test and external key-store backends |
| HTTPS direct transport pinning | Implemented MVP | TLS-wrapped direct Sidecar exchange with certificate fingerprint verification test |
| Production deployment preflight | Implemented MVP | HTTPS Relay/direct endpoint, TLS pair and non-SQLite key backend gate |
| Disposable production preflight demo | Implemented | temporary TLS/helper assets, readiness report and cleanup; does not substitute for remote connectivity or hardware validation |
| External project-key storage | Implemented MVP | `sqlite`, 0600 `file`, macOS `keychain`, operator-managed JSON stdin/stdout `external`, migration, preflight, scope validation and no-secret-output tests; real device integration remains |
| Full project lifecycle UI | Partial | creation, trust, board, invitation inbox and close exist; member removal and advanced recovery controls remain |

## 7. Delivery order

1. Real cross-machine NAT/firewall deployment validation and multi-hop Gossip convergence beyond local process simulation.
2. Production-grade key storage and rotation recovery.
