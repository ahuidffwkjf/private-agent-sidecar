# Project Scoped Trust Specification

## Principle

Trust granted to a Harness for one project is temporary. It is valid only while that local project record is `active`. Closing the project revokes every project-scoped trust grant. A project grant never becomes global trust.

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

The main Sidecar dashboard supports project creation/join, candidate selection, temporary Harness trust, member/status display and project closure. The close action warns that project trust is immediately revoked.
