#!/bin/bash
# Local Mac helper. No production settings, deployment or API key in this file.
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Please install Python 3.12 or newer from https://www.python.org/downloads/macos/ and try again.'
  exit 1
fi
exec python3 "$SCRIPT_DIR/private_mac_test.py"
