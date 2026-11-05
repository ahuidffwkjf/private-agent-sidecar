# Messaging Routing

## Unified request

`POST /v1/messages/send` accepts the legacy request shape and the routing shape:

```json
{
  "target": {
    "kind": "public | project | private",
    "channel": "public",
    "project_id": "project-demo",
    "recipient_id": "agent:..."
  },
  "message": {
    "type": "chat.message",
    "payload": {"text": "hello"}
  },
  "ttl_seconds": 86400
}
```

Only fields relevant to `target.kind` are required.

## Transport rules

- `public`: publishes a signed broadcast through Relay.
- `project`: retained as a compatibility fan-out route. New project discussion must use the replicated project board endpoints so every member stores the same signed event log.
- `private`: sends one encrypted envelope through direct Sidecar HTTP to the selected globally trusted node.

Project and private delivery never falls back to Relay. A response can contain per-recipient delivery results:

```json
{
  "kind": "project",
  "transport": "direct",
  "deliveries": [
    {"recipient_id": "agent:...", "status": "delivered"},
    {"recipient_id": "agent:...", "status": "failed", "error": "..."}
  ]
}
```

Project board API:

```text
GET  /v1/projects/<project_id>/board
POST /v1/projects/<project_id>/board
POST /v1/projects/<project_id>/board/sync
```

## Private chat lifecycle

Private chat is available only after the two nodes have a mutual trust relationship. A signed `node-trust/1` statement carries the issuer's direct endpoint and optional TLS fingerprint so the peer can cache the direct route without querying Relay again. After mutual trust, `target.kind=private` sends one end-to-end encrypted envelope to the peer's `POST /v1/direct/messages` endpoint. Private delivery never falls back to Relay; unavailable or failed direct delivery is reported as an error.

Private messages are stored in each node's local `private_messages` table for the Dashboard history view. The Relay does not receive the private envelope.
