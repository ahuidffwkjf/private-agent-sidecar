"""Foreground entrypoint for an OS-managed, persistent Sidecar network node."""
import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_sidecar.network import private_file
from agent_sidecar.service import serve_sidecar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--network-config', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8802)
    parser.add_argument('--relay', required=True)
    parser.add_argument('--relay-token-file', type=Path)
    args = parser.parse_args()
    parsed = urlparse(args.relay)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in {'http', 'https'}:
        raise ValueError('invalid Relay URL; credentials belong in a private file')
    if parsed.scheme != 'https' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('remote Relay requires HTTPS')
    token = ''
    if args.relay_token_file:
        path = private_file(args.relay_token_file)
        if path.stat().st_size > 16384:
            raise ValueError('token file too large')
        token = path.read_text('utf-8').strip()
        if not token or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('invalid token file')
    serve_sidecar('127.0.0.1', args.port, args.data_dir, args.relay, relay_token=token, network_config=args.network_config)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # URLs, subprocess arguments and local paths can include secret material.
        print('Network node stopped: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
