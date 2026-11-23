#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v npm >/dev/null 2>&1; then
  printf '%s\n' 'Install Node.js 22.12 or later, then run this launcher again.'
  exit 1
fi
npm ci --no-audit --no-fund
exec npm start
