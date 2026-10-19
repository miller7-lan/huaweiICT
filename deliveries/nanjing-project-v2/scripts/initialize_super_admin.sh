#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT/vendor/nexent"
# The official script reads the private deployment password itself. An empty
# first argument avoids placing it in this launch command; false hides output.
bash deploy/docker/create-su.sh "" false
