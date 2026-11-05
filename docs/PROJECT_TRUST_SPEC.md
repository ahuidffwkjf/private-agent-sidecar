# Project Scoped Trust Specification

## Principle

Trust granted to a Harness for one project is temporary. It is valid only while that local project record is `active`. Closing the project revokes every project-scoped trust grant. A project grant never becomes global trust. A participant that accepts/creates its local project view enables Owner-signed member auto-trust by default; it remains limited to that project and does not create transitive global trust.

A separately granted global trust relationship is independent and survives project closure.

## Local project model

```text
projects
  project_id
  name
  owner_id
  status: active | closed
  created_at
  closed_at

project_peers
  project_id
  agent_id
  identity_json
  trusted_at
  revoked_at
```

The identity is pinned for the project. A key change is rejected and requires an explicit rotation workflow.

## APIs

```text
GET  /v1/projects
GET  /v1/projects/<project_id>
POST /v1/projects
POST /v1/projects/<project_id>/invitations
GET  /v1/project-invitations
POST /v1/project-invitations/<invitation_id>/response
POST /v1/projects/<project_id>/members/remove
POST /v1/projects/<project_id>/trust
POST /v1/projects/<project_id>/close
```

Create or join a local project view:

```json
{
  "name": "Experiment A",
  "project_id": "project-a",
  "owner_id": "agent:owner"
}
```

`owner_id` defaults to the local node. A joining member records the remote project owner's agent ID so an authenticated `project.lifecycle.close` message from that owner can close the local project automatically.

The invitation endpoints are the normal membership path. The legacy `/trust` endpoint remains for compatibility and administrative tests. A normal Owner invitation does not add the candidate to `project_members` or `project_peers` until the named invitee returns a valid signed `accept` response.

## Signed membership and auto-trust

The project owner may issue a monotonically increasing, signed membership manifest. Receivers accept only manifests signed by the recorded owner and reject stale epochs or changed member keys. Current and later Owner-signed members receive project-scoped trust automatically after the participant has accepted the project. The setting can be disabled locally without changing global trust.

The manifest includes a deterministic participant-set hash. Project board events bind both the membership epoch and participant-set hash so an event cannot silently move to another project membership configuration.

Nodes retain Owner-signed historical manifests at `/v1/projects/<project_id>/membership/history`. A board event includes the exact historical manifest and Owner signature used for its epoch; receivers verify that proof before accepting delayed or offline events.

Every membership epoch also has a distinct random project content key. The Owner distributes that key only through per-member encrypted envelopes inside the signed manifest. Historical keys are retained only by nodes that were members of that epoch. Removing a member advances the epoch and prevents the removed node from decrypting subsequent board content.

The Sidecar supports `--project-key-backend sqlite|file|keychain|external`. `sqlite` is the development default. `file` uses a 0600 atomic JSON file for migration/testing. `keychain` stores only key material in the macOS Keychain through the `security` command. `external` delegates `save`, `get` and `delete` to an operator-managed TPM/HSM/KMS helper over JSON stdin/stdout; secrets are never placed in argv. SQLite retains only epoch metadata and fingerprints when an external backend is selected. `preflight` validates backend availability and fails closed when an external helper is missing.

`preflight --production` additionally requires HTTPS Relay/direct URLs, a valid TLS certificate/key pair and a non-SQLite project-key backend. This is a deployment gate, not a replacement for a real multi-machine connectivity test.

## Message scope

Encrypted envelopes may contain a signed, authenticated `project_id` header. The project ID is metadata visible to Relay; message type and payload remain encrypted.

A project message is accepted only when:

1. the project exists locally and is active;
2. the sender is globally trusted or temporarily trusted for that project;
3. the envelope signature and ciphertext validation pass.

A closed or unknown project message is rejected and acknowledged so it cannot indefinitely block the inbox.

## Project closure

When the local owner closes a project:

1. `project.lifecycle.close` is sent to active project peers through the encrypted project channel;
2. the owner's local project becomes `closed`;
3. every project peer receives `revoked_at`;
4. members that receive an owner-authenticated close message close their local project and revoke their project grants;
5. subsequent project messages and FL rounds are rejected.

If a peer is offline, it processes the close message when it next polls the Relay. Project trust is never consulted once the local project is closed.

## Federated learning

`RoundConfig` includes `project_id`. Coordinator and Node FL traffic uses that project scope. A Coordinator validates that all participants are trusted for the active project before creating a round. Project closure prevents creation of another project round.

## MCP and UI

MCP tools:

```text
project.create
project.list
project.trust
project.close
```

The main Sidecar dashboard supports project creation/join, candidate selection, temporary Harness trust, member/status display, a replicated project board and project closure. The close action warns that project trust is immediately revoked. See `PROJECT_BOARD_SPEC.md` for board replication and offline behavior.
