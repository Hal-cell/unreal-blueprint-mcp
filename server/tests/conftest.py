"""pytest configuration — v8.2.0 integration harness.

Provides ``requires_ue_editor()`` decorator that gates integration tests on:
1. Environment variable ``BLUEPRINTMCP_INTEGRATION=1`` being set (opt-in)
2. UE editor reachable at ``127.0.0.1:55558`` (the BlueprintMCP plugin's TCP port)

Both checks are cached at module import so collection is cheap even with many
gated tests.

Usage in test files::

    from .conftest import requires_ue_editor

    @requires_ue_editor()
    def test_xxx_against_real_plugin():
        ...

Run modes:
- Default (unit tests only)::
    uv run pytest

- Integration mode (requires running UE editor)::
    BLUEPRINTMCP_INTEGRATION=1 uv run pytest

  Or use the convenience script::
    ../scripts/run_integration_tests.sh
"""

from __future__ import annotations

import functools
import os
import socket

import pytest


@functools.lru_cache(maxsize=1)
def _is_ue_reachable() -> bool:
    """Probe the BlueprintMCP TCP server. Cached so we only connect once per run."""
    try:
        with socket.create_connection(("127.0.0.1", 55558), timeout=2):
            return True
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def _integration_enabled() -> bool:
    return os.environ.get("BLUEPRINTMCP_INTEGRATION") == "1"


def requires_ue_editor(extra_reason: str = ""):
    """Skip the decorated test unless integration mode is enabled AND UE is reachable.

    Args:
        extra_reason: Optional context appended to the skip reason (e.g.
            "needs a BP named BP_TestSpike on disk"). Helpful when a test
            additionally requires specific test fixtures pre-staged.

    Skip reasons:
        - ``BLUEPRINTMCP_INTEGRATION!=1`` (default; CI mode off)
        - ``UE editor not reachable on 127.0.0.1:55558``
    """
    if not _integration_enabled():
        reason = "Integration mode off — set BLUEPRINTMCP_INTEGRATION=1"
        if extra_reason:
            reason = f"{reason} ({extra_reason})"
        return pytest.mark.skip(reason=reason)

    if not _is_ue_reachable():
        reason = "BLUEPRINTMCP_INTEGRATION=1 but UE editor not reachable on :55558"
        if extra_reason:
            reason = f"{reason} ({extra_reason})"
        return pytest.mark.skip(reason=reason)

    # All gates open — return a no-op marker so the test runs normally.
    return pytest.mark.skipif(False, reason="(integration test active)")
