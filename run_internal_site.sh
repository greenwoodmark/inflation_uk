#!/usr/bin/env bash
set -euo pipefail

PORT="${DGV_PORT:-8000}"
python3 tools/build_site.py --target internal

if python3 - "$PORT" <<'PY'
import socket
import sys

try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.5):
        raise SystemExit(0)
except OSError:
    raise SystemExit(1)
PY
then
    echo "Port ${PORT} is already in use; an existing server may already be running at http://127.0.0.1:${PORT}"
    exit 0
fi

exec python3 -m http.server "$PORT" --directory build/internal
