# Cross Harness Adapter Specification

## Multiple Actors Per Node

One node Harness can register multiple actors using `POST /v1/actors` (`name`, optional `role`).
`GET /v1/actors` returns their public profiles. The node's existing `agent_id` remains the trust/signing identity;
each actor has a persistent `actor_id`, deterministic node-local `harness_id`, and node-signed profile.
Profiles are included in signed capability announcements; registration persists even when publishing fails.

`POST /v1/actors/send` accepts `actor_id`, `recipient_node_id`, `recipient_actor_id`, and `text`.
It uses the existing encrypted private direct transport and node trust checks; no Relay fallback.
The receiver verifies the sender's actor profile against the envelope signer and checks the local target actor
before saving the message. `GET /v1/actors/inbox?actor_id=...` filters messages for that actor.
These IDs are routing identities, not independent keys, autonomous model runtimes, or process-isolated permissions.
Local APIs must only be exposed to a trusted Harness/operator; the packaged desktop protects them with its session.

## Optional Training Adapters

Training adapters, their structured contracts and reference server now live in
[packages/federated](../packages/federated/README.md). See the
[training Harness specification](../packages/federated/docs/HARNESS_SPEC.md).

Actor registration and encrypted actor communication remain in the base Sidecar;
they do not require the training extension. Existing local Harness configuration
is preserved, but testing or executing it requires the optional package.
