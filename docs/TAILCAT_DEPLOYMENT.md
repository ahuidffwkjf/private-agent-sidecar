# Tailcat integration for the current Sidecar

## Provenance and compatibility

The recovered local 0.4.0 product copy contained `tailcat-serve-relay.sh`,
`tailcat-forward-relay.sh` and systemd templates. Its saved forwarding log
recorded `18787 -> 8787` and `18788 -> 8788`. These are historical deployment
artifacts, not a branch of this repository. No runtime identities, databases,
private keys or tunnel addresses are imported here.

This adaptation targets **this checkout** and **Tailcat v0.7.0**. The current
Relay is HTTP(S)-only: it has no `--websocket-port`, `--websocket`, or legacy
`deepseek-harness` command. Do not use this launcher against the historical
API-only 0.3/0.4 service or reuse its database without a separate migration.

```text
Sidecar -> https://127.0.0.1:18787
                  |
        Tailcat forward (client key)
                  | WireGuard / direct or DERP
        Tailcat serve (explicit client allowlist)
                  |
       HTTPS Relay 127.0.0.1:8787
```

Only port 8787 is published; 8788 is not opened or forwarded. Local/remote
ports can be changed with `TAILCAT_LOCAL_PORT` / `TAILCAT_RELAY_PORT`. Keep the
remote port identical in the Relay and Tailcat server environments. Do not
stop an existing service to free a port: select unused ports and an independent
database during acceptance.

## Configuration and credentials

Install the same current Sidecar source on both hosts and install Tailcat
v0.7.0 separately from its trusted distribution. `tailcat version` must return
`v0.7.0`. This wrapper neither downloads software nor generates new identities.

Use three example files under `deploy/tailcat/`:

| File | Used by | Important settings |
|---|---|---|
| `relay.env.example` | HTTPS Relay | certificate, key, database, token file, rate limit |
| `server.env.example` | Tailcat server | persistent key file, public client allowlist, address output file |
| `client.env.example` | Tailcat forward / local node | client key, address input file, CA, token, node directory |

Copy them to operator-owned files outside the repository, replace placeholder
paths and set file permissions to 0600. Env files are configuration, not shell
programs. For the foreground examples below, use only your own trusted env
file with shell-compatible assignments (quote paths with spaces):

```sh
set -a
. /absolute/path/to/your.env
set +a
```

Keys are created **once**, by the same OS user and with the same
`HOME`/`XDG_CONFIG_HOME` as the eventual service:

- Server: `tailcat genkey --key=sidecar-relay --fixed-region`. Its output is
  secret: redirect it to a 0600 file in a 0700 directory; do not paste it into
  chat, terminal captures or Git. Do not use `--force` on an existing identity.
- Client: `tailcat genkey --client --key=client-default`. Only its public
  `nodekey:...` is transferred to the server's `TAILCAT_CLIENT_KEYS` allowlist.
- Configure the **absolute** generated `.private.json` path as
  `TAILCAT_KEY_FILE`. Private key and address files must be owned by the
  executing user, mode 0600 (0400 is accepted for read-only inputs), not symlinks.
- Server `TAILCAT_ADDR_FILE` is a file output path in a private 0700 directory.
  The launcher suppresses Tailcat stdout/stderr so its address is not put in
  service logs. Distribute the address file through an authenticated channel.
- Generate a separate strong Relay token into `RELAY_TOKEN_FILE` with mode
  0600. For example, in a private directory: `umask 077` then
  `openssl rand -hex 32 > relay-token` **only if that file does not exist**.
  Transfer that token to authorized clients securely; never copy server keys.

Tailcat's CLI necessarily receives the address in its child process arguments.
Restrict OS account access and do not publish `ps` output or enable shell tracing.

### TLS is still required

Tailcat wraps the TCP stream; it does not terminate Relay TLS. Keep CA and
hostname verification enabled, even inside the encrypted tunnel. Because the
client URL below is `https://127.0.0.1:18787`, the **Relay certificate must include
`IP:127.0.0.1` in SAN**, and the client must trust its issuing CA (or the pinned
self-signed certificate distributed out-of-band). A certificate covering only
the old public IP will fail through this loopback URL. Provision a suitable
certificate before migrating, without overwriting certificates still in use.

`RELAY_CA_FILE` is a public CA/certificate bundle, not the Relay private key.
Do not use `curl -k`, disable Python TLS verification, or assume changing an
HTTP Host header repairs a certificate-name mismatch. If a proxy is configured
on the host, ensure `NO_PROXY=127.0.0.1,localhost` for these local connections.

## Start and check (foreground)

Run from the current repository with its `.venv`. Use separate terminals with
the appropriate env file loaded. Check commands do not start listeners or
create node state:

```sh
# Server, relay.env loaded
.venv/bin/python scripts/tailcat_deploy.py relay --check
.venv/bin/python scripts/tailcat_deploy.py relay

# Server, tailcat-server.env loaded
sh scripts/tailcat-serve-relay.sh --check
sh scripts/tailcat-serve-relay.sh

# Client, tailcat-client.env loaded and address file securely transferred
sh scripts/tailcat-forward-relay.sh --check
sh scripts/tailcat-forward-relay.sh

# Client, another terminal with the same client environment
.venv/bin/python scripts/tailcat_deploy.py probe
.venv/bin/python scripts/tailcat_deploy.py node --check
.venv/bin/python scripts/tailcat_deploy.py node
```

`probe` verifies HTTPS health and authenticated `/v1/agents`, without printing
identities or secrets. It is a network/API check, not proof of peer trust or a
direct NAT path. `--check` validates only local configuration/version/files.
The `node` launcher reads the token from a file and starts the current Sidecar
on `127.0.0.1:8802` (or `SIDECAR_PORT`) with the specified persistent data
directory. It does not modify the existing CLI or put the token in argv.

On Linux, the optional `.service` files in `deploy/tailcat/` assume code at
`/opt/private-agent-sidecar`, service users `agent-relay` / `agent-sidecar`,
and env files in `/etc/agent-sidecar/`. Create those users and directories,
grant only the required file access, and generate keys as the service user.
Install the templates deliberately after verifying no existing unit/port is
being replaced. Verify them with `systemd-analyze verify` on the target host
before enabling. These templates are **not installed automatically**. macOS
uses the foreground launcher; systemd units do not apply there.

## What this does not solve

- Tailcat access is not Sidecar trust: new peers remain pending and require
  explicit identity verification and trust grants.
- Forwarding the Relay does **not** connect private chat or project-board
  endpoints. Those paths must have their own reachable HTTPS endpoints and
  node authorization. Do not advertise the client's loopback forwarding URL
  as a shared peer endpoint or silently send project content through Relay.
- This node launcher provides local administration and Relay connectivity,
  not the full production direct-node / key-store setup. Continue with
  `CROSS_MACHINE_DEPLOYMENT_CHECKLIST.md` for that deployment.
- FL CLI coordinator/node commands in this checkout currently have no Relay
  token flag. Do not disable Relay authentication to accommodate them; wiring
  file-based credentials into those commands is a separate change.
- Do not claim UDP hole punching from successful delivery alone: Tailcat may
  use DERP. Verify the actual path with v0.7.0 `ping --until-direct` in a private
  operator session; never attach its secret address to a public report.

## Acceptance and rollback

1. Check configurations and listening ports: Relay and local forward are
   loopback-only; no unintended 8788 listener is created.
2. Run `probe` from an independent client host, then verify node registration,
   explicit reciprocal trust, and encrypted message delivery in both directions.
3. Confirm a non-allowlisted Tailcat identity cannot reach the published Relay.
   Wrong CA/SAN and wrong Relay tokens must also fail.
4. Stop only the test forwarder: new connections must fail, with no automatic
   public-IP/HTTP fallback. Restart it and retry using the same node identity.
5. Retain sanitized results (ports, versions, pass/fail, message IDs), not keys,
   tokens, addresses or raw process arguments. This repository's tests validate
   configuration and TLS/authentication through a local TCP forward; they do
   **not** substitute for Tailcat cross-machine NAT/DERP acceptance.

Rollback by stopping only the new test services and restoring the previous
operator configuration. Keep original databases and node directories intact.
No schema or peer-trust migration is required by these deployment scripts.

## Local verification record (2026-11-05)

Changed surface: new `scripts/tailcat_deploy.py`, the two Tailcat shell wrappers,
`deploy/tailcat/` templates, `tests/test_tailcat_deploy.py`, this document,
README/runbook links and runtime-secret ignore patterns. Existing product
modules and historical deployment data were not replaced.

| Command / check | Result |
|---|---|
| `.venv/bin/python -m unittest tests.test_sidecar.HttpSecurityTests tests.test_sidecar.RelayAuthTests tests.test_sidecar.NetworkTests -v` | 3 baseline tests passed before edits |
| `.venv/bin/python -m unittest tests.test_tailcat_deploy -v` | 10 deployment tests passed |
| `.venv/bin/python -m unittest discover -s tests -v` | 109 tests passed after edits |
| `.venv/bin/python scripts/run_federated_demo.py` | 10 rounds; accuracy 0.995833, loss 0.046439 |
| `.venv/bin/python scripts/run_federated_network_demo.py` | 3 rounds completed |
| `.venv/bin/python scripts/run_federated_process_demo.py` | 3 rounds completed in independent processes |
| `.venv/bin/python -m agent_sidecar benchmark --config configs/benchmark/small.json --output runs/benchmark-tailcat-deployment-20261105` | 90 simulation runs; clean accuracy 0.995833 |
| Installed v0.7.0 binary with historical client files, `forward --check` | Passed, no listeners started and no secrets printed |
| Shell syntax and `git diff --check` | Passed |

Benchmark configuration and summaries are retained per scenario under
`runs/benchmark-tailcat-deployment-20261105/`, with `matrix.json`,
`comparison.csv` and `comparison.md` at its root. This is an FL regression
baseline, **not** a Tailcat performance/security measurement.

External blocker: server SSH requires interactive password authentication;
the remote services, TLS migration and real tunnel were not modified or tested.
Linux systemd templates also require verification on that target host.
Next priority: stage this current checkout beside the existing server product,
provision matching TLS credentials and run the independent-host acceptance
steps above without changing the live service or its database.
