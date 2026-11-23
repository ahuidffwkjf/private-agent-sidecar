from __future__ import annotations

import argparse
import json
from pathlib import Path

from .optional_fl import available as fl_available, load as load_fl
from .preflight import run_preflight
from .relay import serve_relay
from .service import serve_sidecar


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-sidecar", description="Encrypted communication and collaboration for autonomous agents")
    commands = parser.add_subparsers(dest="command", required=True)

    relay = commands.add_parser("relay", help="run the untrusted ciphertext relay")
    relay.add_argument("--host", default="127.0.0.1")
    relay.add_argument("--port", type=int, default=8787)
    relay.add_argument("--database", type=Path, default=Path("relay.db"))
    relay.add_argument("--tls-cert", type=Path, default=None)
    relay.add_argument("--tls-key", type=Path, default=None)
    relay.add_argument("--access-token", default="")
    relay.add_argument("--rate-limit", type=int, default=0, help="max protected requests per source IP per minute; 0 disables")

    serve = commands.add_parser("serve", help="run one local agent sidecar")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--data-dir", type=Path, required=True)
    serve.add_argument("--relay", default="http://127.0.0.1:8787")
    serve.add_argument("--tls-cert", type=Path, default=None)
    serve.add_argument("--tls-key", type=Path, default=None)
    serve.add_argument("--relay-token", default="")
    serve.add_argument("--direct-endpoint", default="", help="public HTTPS endpoint advertised for direct project/private messages")
    serve.add_argument('--network-config', type=Path, default=None, help='private Tailcat network manager JSON config')
    serve.add_argument("--project-key-backend", choices=("sqlite", "file", "keychain", "external"), default="sqlite", help="storage backend for project epoch content keys")
    serve.add_argument("--project-key-command", nargs="+", default=None, help="external key helper command; receives JSON on stdin and returns JSON on stdout")

    preflight = commands.add_parser("preflight", help="validate local deployment prerequisites without printing secrets")
    preflight.add_argument("--data-dir", type=Path, default=None)
    preflight.add_argument("--harness-config", type=Path, default=None)
    preflight.add_argument("--relay", default=None)
    preflight.add_argument("--tls-cert", type=Path, default=None)
    preflight.add_argument("--tls-key", type=Path, default=None)
    preflight.add_argument("--project-key-backend", choices=("sqlite", "file", "keychain", "external"), default="sqlite")
    preflight.add_argument("--project-key-command", nargs="+", default=None)
    preflight.add_argument("--direct-endpoint", default=None)
    preflight.add_argument("--production", action="store_true", help="enforce HTTPS, TLS certificate pair and non-SQLite project key storage")

    if fl_available():
        load_fl("cli").register_commands(commands)

    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(run_preflight(args.data_dir, args.harness_config, args.relay, args.tls_cert, args.tls_key, args.project_key_backend, args.project_key_command, args.direct_endpoint, args.production), ensure_ascii=False, indent=2))
    elif args.command == "relay":
        serve_relay(args.host, args.port, args.database, args.tls_cert, args.tls_key, args.access_token, args.rate_limit)
    elif args.command == "serve":
        serve_sidecar(args.host, args.port, args.data_dir, args.relay, args.tls_cert, args.tls_key, args.relay_token, args.direct_endpoint, args.project_key_backend, args.project_key_command, args.network_config)
    else:
        load_fl("cli").dispatch(args)


if __name__ == "__main__":
    main()
