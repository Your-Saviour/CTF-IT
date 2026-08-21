"""Opt-in acceptance gate for a disposable, externally supplied green VM."""

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.expo_it_green_live


def test_green_live_prerequisites_are_explicit():
    if os.environ.get("EXPO_IT_GREEN_LIVE") != "1":
        pytest.skip("set EXPO_IT_GREEN_LIVE=1 to run the disposable-VM acceptance gate")
    key_path = os.environ.get("EXPO_IT_GIT_SSH_KEY_PATH")
    target = os.environ.get("EXPO_IT_GREEN_TARGET")
    if not key_path or not target:
        pytest.skip("EXPO_IT_GIT_SSH_KEY_PATH and EXPO_IT_GREEN_TARGET are required")
    key = Path(key_path)
    assert key.is_file(), "EXPO_IT_GIT_SSH_KEY_PATH must be mounted into the test container"
    assert key.stat().st_mode & 0o077 == 0, "the repository deploy key must not be group/world accessible"
    assert "@" in target, "EXPO_IT_GREEN_TARGET must use user@host form"

    # Provisioning is intentionally driven through CTF-IT's event start/retry
    # workflow. This gate validates dangerous external prerequisites up front;
    # the operator then follows TEST_PLAN.md's end-to-end API and network checks.
