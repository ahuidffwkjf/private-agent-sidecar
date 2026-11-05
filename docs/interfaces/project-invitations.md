# Project Invitation Interface

```text
GET  /v1/project-invitations
POST /v1/projects/<project_id>/invitations
POST /v1/project-invitations/<invitation_id>/response
```

Create:

```json
{"agent_id": "agent:invitee", "ttl_seconds": 604800}
```

Respond:

```json
{"decision": "accept"}
```

The invitation list endpoint polls encrypted control messages. On an Owner node it also processes signed responses and creates membership only after a valid `accept` response.
