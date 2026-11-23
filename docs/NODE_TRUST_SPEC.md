# Node-Level Trust Specification

## Principle

Node trust is directional and long-lived. A node becoming authenticated to Relay does not make it trusted by any peer. Relay and third-party trust are never transitive.

A relationship has two independent decisions:

```text
A trusts B
B trusts A
```

Only when both statements are currently `trust` does the UI report `mutual` trust.

## Signed statement

Trust control messages use an end-to-end encrypted Relay mailbox envelope with message type `node.trust.statement`. Relay can route the ciphertext but cannot read the statement. The payload is also independently signed by its issuer and uses schema `node-trust/1`.

A statement includes:

- globally unique statement ID;
- issuer and subject Agent IDs;
- issuer and subject signing-key fingerprints;
- decision: `trust`, `reject`, `block` or `revoke`;
- issue time and nonce;
- Ed25519 signature over the canonical payload.

The receiver resolves the issuer's registered identity, verifies the encrypted envelope and statement signature, confirms that the local node is the named subject, checks key fingerprints and rejects future-dated or malformed statements. Duplicate statement IDs are idempotent; reusing an ID with different content is rejected.

## Flow

1. A selects B and issues `trust`.
2. A locally pins and trusts B's identity.
3. B receives a signed notification but does not automatically trust A.
4. B may accept, reject or block.
5. Accept creates B's independent `trust` statement to A.
6. After A receives it, both dashboards show `mutual`.
7. Either side may later revoke only its own half; the remaining one-way trust is preserved.

## Relationship states

```text
pending
trusting
trusted_by_peer
mutual
rejected_by_peer
revoked
revoked_by_peer
blocked
blocked_by_peer
```

## HTTP API

```text
GET    /v1/peers/trust-notifications
POST   /v1/peers/trust-requests
POST   /v1/peers/trust-response
DELETE /v1/peers/trust/<agent_id>
```

Request trust:

```json
{
  "agent_id": "agent:peer",
  "expected_signing_key": "base64url-public-key"
}
```

Respond:

```json
{
  "agent_id": "agent:requester",
  "decision": "accept"
}
```

`decision` may be `accept`, `reject` or `block`.

## Compatibility

The legacy local allowlist endpoints remain available, but the Dashboard and Trust Manager use the signed request/response flow. Legacy local trust is displayed as one-way `trusting` until a reciprocal signed statement arrives.
