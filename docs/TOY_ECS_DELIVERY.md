# ECS Toy 0.3.3 delivery record

## Changed scope

- `agent_sidecar/online.py`: explicit ECS ciphertext transport, signed delivery
  receipts, persistent queued messages, consent/trust rechecks, online discovery.
- `agent_sidecar/online_desktop.py`, `agent_sidecar/desktop.py`: one user per local
  desktop, separate ECS workspace, HTTPS access provisioning and background sync.
- `agent_sidecar/service.py`: queued membership delivery is not completed delivery;
  receive traces identify the actual transport.
- `agent_sidecar/toy_directory.py`: direct invitations for main tasks, preserving
  parent-members-only subgroup rules and terms-bound explicit acceptance.
- `agent_sidecar/web/app.js`, `agent_sidecar/web/toy.js`: online/offline contacts,
  task invitations, pending-delivery state and live invitation candidate lookup.
- `desktop/main.cjs`, `desktop/policy.cjs`, `desktop/preload.cjs`, settings files:
  ECS default, explicit private access-file import, optional local demo, no tokens
  in packaged code. `desktop/package*.json`: 0.3.3 development version.
- `scripts/ecs_relay.py`, `deploy/ecs/*`: isolated service account, TLS-only Relay,
  strong secret file permissions, rate/concurrency limits, systemd restrictions.
- `scripts/ecs_toy_fixture.py`, `scripts/check_ecs_toy.py`, online desktop smoke
  script and new Python/JS tests: synthetic and real-ECS acceptance.
- `AGENTS.md`, `CONTINUOUS_GOAL.md`, `docs/TOY_ECS_GUIDE.md`: active architecture,
  explicit encrypted Relay transport and operator/user instructions.

## ECS installation

Host: `189.24.102.37`, Ubuntu 22.04 x64, Python 3.10.12. Existing 80/443 services
were left untouched. `sidecar-ecs-relay.service` runs as `sidecar-ecs`, starts at
boot, and serves HTTPS on port 8787. Credentials and keys are under
`/etc/sidecar-ecs`; source and venv under `/opt/sidecar-ecs`; SQLite under
`/var/lib/sidecar-ecs`. Configuration check and loopback TLS health passed.

The first start exposed private umask traversal permissions and strict TLS
certificate extension requirements. Directory permissions and complete CA/server
extensions were repaired without disabling verification or changing private keys.

This is a new ECS database, not a complete migration of OrangePi records. The
OrangePi is unreachable; its historical data was not deleted, reset or replaced.
No SSH password, access token, private key or research/chat database is published.

## Acceptance

Baseline: `python -m unittest discover -s tests -p test_sidecar.py -v` passed.
New tests exercise TLS config failure, credential permissions, single-user
runtime, identity persistence, explicit consent, invitations, membership delivery,
private/group ciphertext, queued restart, revocation and forged receipts.

Commands:

```sh
.venv/bin/python -m unittest discover -s tests -v
npm --prefix desktop test
node --check desktop/main.cjs
node --check agent_sidecar/web/app.js
node --check agent_sidecar/web/toy.js
node --experimental-vm-modules scripts/check_dsh_toy_plugin.mjs
npm --prefix desktop run dist
node desktop/scripts/online-smoke.cjs
```

Final local results: 174 Python tests passed in 93.272 seconds; six JavaScript
policy/settings tests passed; DSH stub-boundary tests passed (no real model).
The final packaged macOS ARM64 app passed nine ECS UI checks in 32.276 seconds,
including first-start provisioning, two distinct user apps, explicit task invite
acceptance, encrypted group delivery, restart, and desktop/390px/320px layouts.
Screenshots and the machine-readable report are saved with the delivery evidence.

Real ECS benchmark: `runs/ecs-toy-0.3.3/ecs-tunnel-acceptance.json`. Two synthetic
local clients communicated with the actual ECS through a temporary SSH tunnel.
TLS chain/hostname, unauthorized-directory rejection, online discovery, directed
task invitation, explicit acceptance, delivered membership, encrypted group/private
communication and restart passed. Join took 13,640 ms; scenario 76,468 ms.
Test task was cancelled and test profiles withdrew collection consent afterward.
These are not public-port, multi-network-client or real-model acceptance results.

## Remaining blockers

Direct Mac-to-ECS TCP 8787 times out. ECS loopback HTTPS works, host INPUT policy
is ACCEPT, and UFW is inactive. Cloud security-group/port forwarding or upstream
network ingress remains to be configured and independently retested. No existing
443 service was overwritten to bypass this blocker. The local SSH tunnel is only
for explicit test evidence, not a deployed client dependency.

Public 0.3.2 remains the old four-user demo. Do not advertise it as the ECS
client. Do not claim a four-platform 0.3.3 release until native CI reruns succeed.
Desktop research data remains local, separate from the ECS Relay database;
central collection is not newly deployed. Publicly trusted signing/notarization,
real DSH, historical migration and independent-network tests remain separate.

## Next priority

Allow inbound TCP 8787 in the ECS console (or provision a separate approved HTTPS
ingress), verify public TLS and real-user join, then publish the cross-platform
ECS release with separate private client provisioning.
