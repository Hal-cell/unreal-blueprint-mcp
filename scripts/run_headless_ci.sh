#!/usr/bin/env bash
# v9.6.0 — Fully headless integration-test run.
#
# Boots UnrealEditor-Cmd with our BlueprintMCPRun commandlet (no GUI, no RHI),
# waits for the TCP server to come up, runs pytest, then sends shutdown_editor
# and waits for the process to exit. Designed for CI (GitHub Actions, etc.)
# but also useful locally when you just want clean test runs without an
# interactive editor window.
#
# Prerequisites:
#   - UE 5.4 installed (default: /Users/Shared/Epic Games/UE_5.4)
#   - The TESTMCP project (or any project with BlueprintMCP symlinked in)
#   - Dylib already built (run scripts/build_plugin.sh first if not, or
#     UnrealEditor-Cmd will attempt to build on first launch)
#
# Overrides:
#   UE_ROOT     — UE engine install dir (default: /Users/Shared/Epic Games/UE_5.4)
#   UE_PROJECT  — Path to .uproject (default: TESTMCP)
#   BLUEPRINTMCP_HOST/PORT — TCP location (default 127.0.0.1:55558)
#   HEADLESS_TIMEOUT_SEC   — Max seconds to wait for UE boot (default 180)
#
# Exit codes:
#   0   all tests passed
#   1   UE failed to boot / TCP never came up
#   N   N pytest failures
#   126 UE exited before tests finished
#
# Run from anywhere in the repo.

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
SERVER_DIR="${REPO_ROOT}/server"

UE_ROOT="${UE_ROOT:-/Users/Shared/Epic Games/UE_5.4}"
UE_PROJECT="${UE_PROJECT:-/Users/xuchenghao/Documents/Unreal Projects/TESTMCP/TESTMCP.uproject}"
HOST="${BLUEPRINTMCP_HOST:-127.0.0.1}"
PORT="${BLUEPRINTMCP_PORT:-55558}"
HEADLESS_TIMEOUT_SEC="${HEADLESS_TIMEOUT_SEC:-180}"

UE_CMD="${UE_ROOT}/Engine/Binaries/Mac/UnrealEditor-Cmd"
if [[ ! -x "${UE_CMD}" ]]; then
    echo "ERROR: UnrealEditor-Cmd not found at ${UE_CMD}" >&2
    echo "       Set UE_ROOT to your UE install root." >&2
    exit 1
fi
if [[ ! -f "${UE_PROJECT}" ]]; then
    echo "ERROR: project not found at ${UE_PROJECT}" >&2
    echo "       Set UE_PROJECT to your .uproject path." >&2
    exit 1
fi

# ── 1. Boot UE in commandlet mode ─────────────────────────────────────────────
UE_LOG="${REPO_ROOT}/.headless-ue.log"
echo "[v9.6.0 headless harness] Launching UnrealEditor-Cmd..."
echo "    project : ${UE_PROJECT}"
echo "    log     : ${UE_LOG}"
"${UE_CMD}" \
    "${UE_PROJECT}" \
    -run=BlueprintMCPRun \
    -nullrhi \
    -unattended \
    -nopause \
    -nosplash \
    -nosound \
    > "${UE_LOG}" 2>&1 &
UE_PID=$!
echo "    pid     : ${UE_PID}"

cleanup() {
    if kill -0 "${UE_PID}" 2>/dev/null; then
        echo "[v9.6.0 headless harness] Cleaning up: sending shutdown_editor..."
        python3 -c "
import socket, json
try:
    with socket.create_connection(('${HOST}', ${PORT}), timeout=3) as s:
        s.sendall(b'{\"command\":\"shutdown_editor\"}\n')
        s.recv(1024)
except OSError:
    pass
" || true
        # Give UE up to 15s to exit cleanly, then SIGTERM, then SIGKILL.
        for _ in $(seq 1 30); do
            kill -0 "${UE_PID}" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "${UE_PID}" 2>/dev/null; then
            echo "[v9.6.0 headless harness] Graceful shutdown timed out; sending TERM..."
            kill -TERM "${UE_PID}" 2>/dev/null || true
            sleep 3
            kill -9 "${UE_PID}" 2>/dev/null || true
        fi
    fi
}
trap cleanup EXIT INT TERM

# ── 2. Wait for TCP port ──────────────────────────────────────────────────────
echo "[v9.6.0 headless harness] Waiting for TCP ${HOST}:${PORT} (timeout ${HEADLESS_TIMEOUT_SEC}s)..."
DEADLINE=$(($(date +%s) + HEADLESS_TIMEOUT_SEC))
while ! nc -z "${HOST}" "${PORT}" 2>/dev/null; do
    if [[ $(date +%s) -ge ${DEADLINE} ]]; then
        echo "ERROR: TCP port never came up after ${HEADLESS_TIMEOUT_SEC}s. Tail of UE log:" >&2
        tail -50 "${UE_LOG}" >&2
        exit 1
    fi
    if ! kill -0 "${UE_PID}" 2>/dev/null; then
        echo "ERROR: UnrealEditor-Cmd exited before TCP came up. Tail of log:" >&2
        tail -50 "${UE_LOG}" >&2
        exit 126
    fi
    sleep 2
done
echo "  → TCP reachable"

# ── 3. Run pytest integration tests ───────────────────────────────────────────
cd "${SERVER_DIR}"
echo "[v9.6.0 headless harness] Running integration tests..."
echo "    (BLUEPRINTMCP_HEADLESS=1 skips GUI-only tests via @skip_if_headless)"
echo ""
set +e
BLUEPRINTMCP_INTEGRATION=1 BLUEPRINTMCP_HEADLESS=1 uv run pytest tests/test_server.py \
    -k "_against_real_plugin" \
    --tb=short \
    "$@"
PYTEST_EXIT=$?
set -e

# cleanup trap will send shutdown_editor and reap UE
exit "${PYTEST_EXIT}"
