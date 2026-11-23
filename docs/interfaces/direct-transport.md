# Direct Transport

## Endpoints

```text
GET  /v1/direct/healthz
POST /v1/direct/messages
```

The direct endpoint accepts:

```json
{
  "scope": "private | project | board | membership",
  "envelope": {"protocol": "agent-message/1", "...": "..."}
}
```

The envelope remains end-to-end encrypted. The receiver validates the sender identity, recipient, signature, expiry, replay state and scope authorization before saving it locally.

`board` carries batches of signed `project-board/1` events. The direct sender may transport an event authored by another current member, but each event is verified against the Owner-signed membership manifest and the original author's signing key before it enters the local replica.

## Discovery

Node capability broadcasts may contain:

```json
{
  "direct": {
    "enabled": true,
    "endpoint": "https://node.example:8801",
    "tls_fingerprint": "sha256 hex fingerprint",
    "protocol": "sidecar-direct/1"
  }
}
```

No private keys, Relay tokens, API keys or local paths may appear in capabilities.

## Security

Production direct transport requires HTTPS and operator-managed certificate fingerprint pinning. HTTP is only for local tests. Direct failures are recorded locally and are not retried through Relay.

## Private chat authorization

The `private` scope requires a mutual node-trust relationship. A one-sided local trust decision is insufficient for the new Dashboard private-chat selector; legacy direct callers remain compatible when both sides use the older local `trust()` API. The receiver enforces the same trust boundary before decrypting and persisting the message.
