# Project Auto Trust

Creating or accepting a local project view enables project-member auto-trust by default after the Owner identity is pinned:

```text
POST /v1/projects/<project_id>/auto-trust
{"enabled": true}
```

The endpoint remains available to re-enable or disable the local policy explicitly.

When enabled:

- current verified project members receive project-scoped trust;
- later Owner-signed membership additions receive project-scoped trust automatically;
- the grant never enters the global trust allowlist;
- closing the project revokes the project grants.

Only an accepted Owner-signed membership manifest can introduce members. Relay trust and another member's global trust do not transitively authorize a project peer.
