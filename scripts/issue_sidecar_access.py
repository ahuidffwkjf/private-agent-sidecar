"""Create a private device-bound access bundle after administrator approval."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sidecar_setup import ROOT, SetupError, digest_source, json_read, validate_access, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--address-file', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--ca-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--approved', action='store_true', help='confirm this public key has been approved and added to the Tailcat server allowlist')
    parser.add_argument('--relay-port', type=int, default=8787)
    parser.add_argument('--local-port', type=int, default=18787)
    parser.add_argument('--node-port', type=int, default=8802)
    args = parser.parse_args()
    try:
        request = json_read(args.request)
        key = request.get('tailcat_client_public_key', '')
        if request.get('schema') != 'sidecar-enrollment/1' or not re.fullmatch(r'nodekey:[0-9a-f]{64}', key):
            raise SetupError('Invalid enrollment request')
        if request.get('source_sha256') != digest_source(ROOT):
            raise SetupError('Client request and this distribution differ; align versions first')
        if not args.approved:
            raise SetupError('Administrator must approve this device and add its public key to the server allowlist first')
        if args.output.exists() or args.output.is_symlink():
            raise SetupError('Refusing to overwrite an access bundle')
        bundle = {'schema': 'sidecar-access/1', 'tailcat_client_public_key': key,
                  'source_sha256': request['source_sha256'],
                  'tailcat_address': args.address_file.read_text().strip(),
                  'relay_token': args.token_file.read_text().strip(),
                  'relay_ca_pem': args.ca_file.read_text(), 'relay_port': args.relay_port,
                  'local_port': args.local_port, 'node_port': args.node_port,
                  'issued_at': int(time.time())}
        validate_access(bundle, request)
        write_json(args.output, bundle)
        print(json.dumps({'status': 'issued', 'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))
        return 0
    except (SetupError, OSError, ValueError) as exc:
        print(json.dumps({'status': 'failed', 'reason': str(exc) if isinstance(exc, SetupError) else type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
