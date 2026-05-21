#!/usr/bin/env bash
# v8.2.0 — Run integration tests against a running UE editor.
#
# Prerequisites:
#   1. UE editor open with TESTMCP.uproject (or any project with the
#      BlueprintMCP plugin symlinked into <Project>/Plugins/).
#   2. Plugin's TCP server listening on 127.0.0.1:55558. Check the Output Log
#      for "TCP server listening on 0.0.0.0:55558" after editor startup.
#
# What it does:
#   - Probes :55558 with a short timeout to make sure UE is reachable.
#   - Sets BLUEPRINTMCP_INTEGRATION=1 to unlock @requires_ue_editor()-gated tests.
#   - Runs pytest scoped to integration tests (the *_against_real_plugin family).
#
# Exit codes:
#   0  all integration tests passed
#   1  UE not reachable
#   N  N pytest failures
#
# Run from anywhere in the repo (resolves repo root via $(git rev-parse)).

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
SERVER_DIR="${REPO_ROOT}/server"

HOST="${BLUEPRINTMCP_HOST:-127.0.0.1}"
PORT="${BLUEPRINTMCP_PORT:-55558}"

# ── 1. Probe UE ───────────────────────────────────────────────────────────────
echo "[v8.2.0 harness] Probing UE plugin at ${HOST}:${PORT}..."
if ! python3 -c "
import socket, sys
try:
    with socket.create_connection(('${HOST}', ${PORT}), timeout=2):
        print('  → reachable')
except OSError as e:
    print(f'  → NOT reachable: {e}', file=sys.stderr)
    sys.exit(1)
"; then
    cat >&2 <<EOF

UE editor not reachable on ${HOST}:${PORT}.

Fix:
  1. Open <YOUR_UE_PROJECT>/<name>.uproject in UE Editor.
  2. Wait for editor load to complete; check Output Log for:
       LogBlueprintMCP_TCP: TCP server listening on 0.0.0.0:55558
  3. Re-run this script.

If port is not 55558 (rare), override with:
  BLUEPRINTMCP_PORT=12345 $0

EOF
    exit 1
fi

# ── 2. Run integration tests ──────────────────────────────────────────────────
cd "${SERVER_DIR}"

echo "[v8.2.0 harness] Running integration tests..."
echo "    (BLUEPRINTMCP_INTEGRATION=1 → unlocks @requires_ue_editor() gates)"
echo ""

# Note: caller can pass extra pytest args via $@ (e.g. -v, --tb=long, -k "specific_test")
BLUEPRINTMCP_INTEGRATION=1 exec uv run pytest tests/test_server.py \
    -k "_against_real_plugin" \
    --tb=short \
    "$@"
