# Project Membership

A project Owner controls the signed membership manifest. A member manifest contains the project ID, Owner ID, monotonically increasing epoch, member identities, direct endpoints, roles and the Owner signature.

```text
GET  /v1/projects/<project_id>/membership
POST /v1/projects/<project_id>/membership
GET  /v1/projects/<project_id>/membership/history
GET  /v1/projects/<project_id>/candidates
GET  /v1/projects/<project_id>/gossip-peers?probe=true
```

Only the Owner identity may sign membership changes. Receivers reject an invalid Owner signature, an Owner mismatch or a non-increasing epoch. Membership manifests are delivered through the direct `membership` scope and are stored locally.

Knowing a project ID is not sufficient to join or gain project trust.
