# Scripts

Operational tooling for the unreal-blueprint-mcp project.

## `run_integration_tests.sh` — v8.2.0 headless harness

Run pytest's integration tests against a live UE editor instance.

### Usage

```bash
# 1. Open <YOUR_UE_PROJECT> in UE Editor (with BlueprintMCP plugin loaded)
# 2. Wait for: LogBlueprintMCP_TCP: TCP server listening on 0.0.0.0:55558
# 3. From repo root or any subdirectory:
./scripts/run_integration_tests.sh

# With pytest flags:
./scripts/run_integration_tests.sh -v --tb=long

# Filtered to a single test:
./scripts/run_integration_tests.sh -k test_v8_agentic_loop
```

### What gates the tests

The `@requires_ue_editor()` decorator (defined in `server/tests/conftest.py`)
checks two conditions before unlocking a test:

1. `BLUEPRINTMCP_INTEGRATION=1` env var is set (opt-in — the script sets this)
2. UE editor reachable on `127.0.0.1:55558` (probed once per pytest run, cached)

If either fails, the test is skipped with a clear reason. So in normal unit-test
mode (no env var), all 16+ integration tests skip cleanly.

### Custom host/port

For multi-editor setups or running over LAN (rare):

```bash
BLUEPRINTMCP_HOST=192.168.1.50 BLUEPRINTMCP_PORT=55560 ./scripts/run_integration_tests.sh
```

### CI integration (future)

UE 5.4 binaries are licensed + ~25 GB, so cloud CI (GitHub Actions hosted runners)
is impractical. A **self-hosted runner** with UE installed is the realistic path:

```yaml
# .github/workflows/integration.yml (sketch — not yet active)
name: Integration tests
on: [push, pull_request]
jobs:
  integration:
    runs-on: [self-hosted, macos, ue-5.4]
    steps:
      - uses: actions/checkout@v4
      - name: Rebuild plugin
        run: |
          rm -rf plugin/BlueprintMCP/Binaries plugin/BlueprintMCP/Intermediate
          "/Users/Shared/Epic Games/UE_5.4/Engine/Build/BatchFiles/Mac/Build.sh" \
            TESTMCPEditor Mac Development \
            -Project="$HOME/Documents/Unreal Projects/TESTMCP/TESTMCP.uproject" \
            -waitmutex
      - name: Launch UE in background
        run: |
          open -na UnrealEditor "$HOME/Documents/Unreal Projects/TESTMCP/TESTMCP.uproject"
          sleep 30  # wait for editor + plugin TCP server to come up
      - name: Run integration tests
        run: ./scripts/run_integration_tests.sh -v
      - name: Tear down
        if: always()
        run: osascript -e 'tell application "UnrealEditor" to quit'
```

Until a self-hosted runner exists, integration tests run locally only.
