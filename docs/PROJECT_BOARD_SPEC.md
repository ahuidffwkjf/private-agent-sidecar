# Distributed Project Board Specification

## Principle

Each project has one logical public-to-members board. The board is not hosted by the project Owner and is not a collection of private member chats. Every active member stores a local replica of the same signed event log. The Owner may be offline without preventing other members from reading locally available events, creating new events or synchronizing with other reachable members.

Relay is used for identity discovery, capability broadcasts and initial project coordination. Relay does not carry, store or decrypt board events.

## Authorization

A board operation is accepted only when:

1. the local project exists and is active;
2. the sender is trusted for that project;
3. the event author is in the current Owner-signed membership manifest;
4. the event `membership_epoch` and `participant_set_hash` match that manifest;
5. the event content hash and Ed25519 signature validate;
6. the direct encrypted envelope passes identity, recipient, expiry and replay checks.

Accepting a project membership enables project-member auto-trust by default. This remains project-scoped and never modifies the global trusted-peer allowlist.

## Event model

Board changes are immutable signed events using protocol `project-board/1`. Supported event types are:

```text
post reply edit delete announcement proposal task decision
member_joined member_left member_removed membership_updated board_archived
```

An event includes a globally unique event ID, project and board IDs, membership epoch, participant-set hash, author ID, logical clock, creation time, parent event ID, content type, content hash and author signature. Editing or deleting content appends another event; it does not overwrite the signed original.

## Replication and offline behavior

A locally created event is committed to the local SQLite replica before network delivery is attempted. Delivery failure therefore leaves a usable local event marked for later synchronization instead of rolling back the post.

Members exchange board event batches over the direct Sidecar encrypted transport with scope `board`. Duplicate event IDs are idempotent. Reusing an event ID with different content is rejected. Periodic synchronization republishes the local event set to reachable current members. Persistent all-to-all connections are not required.

When an Owner or any other member is offline:

- online members continue reading their local replicas;
- online members continue posting and replying;
- reachable members continue synchronizing directly;
- the offline member catches up after reconnecting and a sync pass completes.

## HTTP surface

```text
GET  /v1/projects/<project_id>/board
POST /v1/projects/<project_id>/board
POST /v1/projects/<project_id>/board/sync
GET  /v1/projects/<project_id>/membership/history
POST /v1/projects/<project_id>/gossip-settings
```

Create an event:

```json
{
  "event_type": "post",
  "content_type": "application/json",
  "content": {"text": "dataset preparation is complete"},
  "parent_event_id": ""
}
```

The create response returns the locally committed event and per-member best-effort synchronization results. A failed remote delivery does not mean the local post failed.

Membership history returns Owner-signed manifests retained for every known epoch. Board events carry the exact manifest and signature used at creation, so a member can validate an older event even after the project has advanced to a newer membership epoch. `event_set_root` is a deterministic binary Merkle root over sorted event IDs and is exposed in board summaries.

Project members do not need a full mesh. `gossip_fanout` selects a deterministic cyclic subset of current members for periodic synchronization. The default fanout is 2 and can be changed with:

```json
{"gossip_fanout": 1}
```

Manual full repair ignores the fanout and contacts every current member. Periodic Merkle synchronization uses only the selected neighbors, allowing events to propagate over multiple cycles without requiring every node to connect to every other node.

Neighbor selection is health-aware: advertised endpoint availability, recent direct-board successes/failures and capability-reported p95 latency are scored before selecting the configured fanout. Success/failure scores are persisted per project/member pair and decay with a one-hour half-life, so an old outage does not permanently poison a healthy peer. Operators can inspect scores with `GET /v1/projects/<project_id>/gossip-peers`; adding `?probe=true` performs a bounded direct `/v1/direct/healthz` probe with HTTPS fingerprint pinning. Selection remains deterministic for equal scores so nodes do not thrash between peers.

The health metrics survive Sidecar restart in `project_gossip_peer_metrics`. Each successful or failed board delivery updates persistent raw counters and timestamps; selection applies the decay function at read time rather than rewriting history, so a long-idle peer naturally recovers without losing auditability.

The last 100 delivery attempts are retained per member pair as a bounded sliding window. Neighbor selection uses the most recent 20 attempts with the same time decay; the durable raw counters and timestamps remain available for audit. Old attempts are pruned, preventing long-running projects from growing an unbounded health log.

A recent direct delivery failure also activates a 30-second cooldown for non-probed selection. During cooldown the peer remains visible but is deprioritized; a successful live `probe=true` health check can immediately restore availability priority. This prevents tight retry loops while allowing a recovered endpoint to return quickly.

Each Merkle exchange carries a `gossip_id`, `hop_count` and `max_hops`. Receivers reject malformed or expired propagation scopes, and send-side event-ID history is bounded to 5,000 IDs per member pair. The durable board event log remains the source of truth, while the bounded propagation window prevents repeated forwarding loops.

When a member is removed, the Owner sends the new signed membership manifest once to the removed node as a revocation notice before it loses local project authorization. The manifest contains no new content-key envelope for that node. The removed node can then immediately revoke its own board access while retaining only the historical keys it legitimately possessed.

Board content is encrypted with a random 32-byte project content key for the event's membership epoch. The Owner creates a fresh key for every new membership epoch and places a separately encrypted key envelope for each current member inside the Owner-signed manifest. A member can decrypt only the epochs for which it received a key envelope. A newly joined member does not automatically receive earlier epoch content keys; a removed member never receives the new epoch key.

## Current compatibility limit

The first board protocol version now retains Owner-signed manifest history and embeds the exact historical manifest proof in each new event. Periodic synchronization exchanges deterministic bucket-range Merkle summaries over an encrypted request/response exchange, sends only events from differing buckets, and repeats until both roots converge. The manual `board/sync` operation can force a full repair. Partial-neighbor Gossip scheduling remains a next protocol extension.
