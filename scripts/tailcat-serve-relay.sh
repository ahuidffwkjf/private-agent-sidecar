#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${SIDECAR_PYTHON:-$ROOT/.venv/bin/python}" "$ROOT/scripts/tailcat_deploy.py" serve "$@"
