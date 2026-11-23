# Sidecar Interfaces

This directory is the community extension point for HTTP and message interfaces.

## Routing contract

```text
Public        -> Relay broadcast
Project board -> signed local event replica + direct Sidecar HTTPS synchronization
Private chat  -> direct HTTPS to one globally trusted Harness
```

Project and private messages are fail-closed: the sender never falls back to Relay when a direct endpoint is missing, unreachable, or fails identity/TLS verification.

Pending peers use the encrypted node-trust control interface in `node-trust.md`. This control message does not authorize ordinary application traffic; only an explicit local trust decision changes the allowlist.

Project membership starts through `project-invitations.md`. Selecting a candidate sends an invitation; the Owner does not add it to the signed member set until a valid invitee-signed acceptance arrives.

## Contributing an interface

1. Add or update a Markdown document in this directory.
2. Include request/response examples, authorization rules, failure behavior and compatibility notes.
3. Add JSON examples under `examples/` when the shape is non-trivial.
4. For a new protocol or extension, start with a proposal under `proposals/` and do not change the core contract without a compatibility note.

The current implementation is a research preview. HTTPS, certificate pinning, reachable node endpoints and deployment-level firewall/NAT configuration are required for production direct transport.
