# Sidecar Toy Preview 0.3.0

Release ID: `toy-v0.3.0-20261123`.

This is a source-based experimental preview, not a signed desktop binary or a
production release. The main entry is `README.md` / `docs/TOY_USER_GUIDE.md`.

## Included

- Existing WeChat-style UI with a no-reward task hall, explicit applications and
  approvals, independent hierarchical task groups and submission/review.
- Node identities, explicit trust, encrypted direct chats and signed group boards.
- Per-node task summary jobs and structured publisher Harness callbacks. The
  local demo uses an explicitly labeled extractor, not a real model response.
- Separate opt-in research database and administrator export/delete tooling.
- Generic HTTP/MCP contracts, DSH plugin source, regression tests and local demos.
- Desktop source and build instructions as optional components, not a prebuilt app.

## Privacy and Distribution

Full content collection is enabled only after experiment consent in toy mode.
Private/group messages in such experiments are uploaded to the research
collector; do not use this preview for confidential conversations. Standard mode
does not upload these contents. Redaction is not a guarantee against all secrets.

The ZIP uses a source allowlist and includes a checksum manifest. It excludes
runtime identities, SSH keys, Relay/research tokens, TLS private keys, databases,
research/chat logs, local paths/host provisioning, virtual environments, third-party
dependencies and optional FL training code. Install requires Python dependency
downloads; native desktop builds additionally require their documented toolchain.

## Known Boundaries

- Real DSH execution/summary quality still needs credentials and an approved
  isolated summary environment; plugin tests stub SDK boundaries.
- A single trusted Relay maintains toy task metadata. There is no distributed
  task-directory consensus, payment, or automatic decision-policy training.
- Local UI can remain reachable while remote Relay is not. Remote devices need
  mutually reachable networks, proper TLS, provisioning and direct chat paths.
- The four-user local launcher uses POSIX process tooling; Windows end-to-end
  launcher behavior is not claimed verified.

## Verification

The source revision is verified with the full Python suite, plugin boundary
checks, a four-process toy scenario and clean-extracted-package installation.
Final exact results are attached in `VALIDATION.md` alongside the release bundle.
No real user data or credentials are included as examples.
