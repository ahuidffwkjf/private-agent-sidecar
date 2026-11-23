# Project Invitation Specification

## Principle

A discovered node is never added to a project merely because an Owner selected it. Membership begins only after the exact invited identity accepts a signed invitation. Until acceptance, the Owner's signed membership manifest and project trust set remain unchanged.

An invitation may target:

- a node with mutual long-term trust (`mutual-node-trust`); or
- another discovered node requesting only project-scoped temporary trust (`project-temporary-trust`).

Both flows require explicit acceptance. The second flow never creates global trust.

## Control messages

Invitations and responses use end-to-end encrypted Relay mailbox control messages because the nodes may not yet have a project-authorized direct connection.

Invitation message type:

```text
project.invitation
```

Response message type:

```text
project.invitation.response
```

The signed `project-invitation/1` payload includes the invitation ID, project identity, Owner and invitee IDs, invitation kind, current membership epoch and participant-set hash, current Owner-signed membership manifest, issue/expiry times and nonce.

The independently signed response includes the invitation and project IDs, Owner and invitee IDs, `accept` or `reject`, issue time and nonce.

## Acceptance flow

1. Owner sends the invitation; the invitee is not a member.
2. Invitee verifies the encrypted envelope, Owner identity, invitation signature, embedded membership signature, target identity, expiry and participant-set hash.
3. Invitee accepts or rejects.
4. Acceptance creates the invitee's local project view and project-scoped trust for the Owner.
5. Owner verifies the invitee-signed response.
6. Only then does Owner add the invitee, advance and sign the membership manifest, and distribute it to current members.
7. The accepted node applies the manifest and automatically grants project-scoped trust to the signed member set.

If the invitee is temporarily offline when the Owner processes acceptance, the Owner records the accepted membership but keeps the invitation in `accepted` state. Subsequent invitation polling retries direct membership-manifest delivery and marks it `completed` only after the invitee acknowledges the manifest.

## HTTP API

```text
GET  /v1/project-invitations
POST /v1/projects/<project_id>/invitations
POST /v1/project-invitations/<invitation_id>/response
```

## Security behavior

- only the recorded project Owner can issue an invitation;
- only the named invitee can respond;
- expired, malformed, mistargeted or incorrectly signed control messages are acknowledged and rejected;
- duplicate IDs are idempotent and conflicting ID reuse is rejected;
- rejected invitations never change project membership;
- project membership and temporary trust are created only after Owner processes a valid acceptance;
- Relay routes ciphertext but cannot read invitation content.
