# Node Trust Interface

Node trust requests are encrypted Relay-mailbox control messages, not public broadcasts and not ordinary trusted-peer messages. This lets a pending peer notify another node without being authorized to send application data.

```text
GET    /v1/peers/trust-notifications
POST   /v1/peers/trust-requests
POST   /v1/peers/trust-response
DELETE /v1/peers/trust/<agent_id>
```

The notification endpoint polls pending Relay ciphertext, accepts only valid `node.trust.statement` control messages from otherwise untrusted identities, and leaves unrelated untrusted application messages unauthorized. Accepting a request creates a new reciprocal statement; it does not transform or reuse the requester's decision.
