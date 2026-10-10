"""Bound the Docker readiness probe even when Desktop is waiting for WSL."""

import subprocess
import sys


try:
    result = subprocess.run([sys.argv[1], "info", "--format", "{{.ServerVersion}}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    sys.exit(result.returncode)
except (OSError, subprocess.TimeoutExpired):
    print("Docker engine unavailable; WSL 2 initialization is required.")
    sys.exit(1)
