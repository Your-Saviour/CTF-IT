"""Tests for the 4 new verification types added for payload modules:
file_absent, file_hash_changed, cron_not_present, user_not_exists.
"""
import pytest

from api.routes.verify import extract_and_check
from api.schemas import SnapshotPayload

STORED_FLAG = "test-flag"


def _snapshot(**overrides) -> SnapshotPayload:
    """Build a minimal SnapshotPayload with optional field overrides."""
    defaults = dict(
        user_id="testuser",
        flag=STORED_FLAG,
        build_state={},
        file_permissions={},
        file_contents={},
        services={},
        packages=[],
        listening_ports=[],
        shadow_hashes={},
    )
    defaults.update(overrides)
    return SnapshotPayload(**defaults)


# ── file_absent ──────────────────────────────────────────────────────────────

class TestFileAbsent:
    verification = {"type": "file_absent", "path": "/tmp/payload.sh"}

    def test_file_gone(self):
        snap = _snapshot(file_existence={"/tmp/payload.sh": False})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is True

    def test_file_still_present(self):
        snap = _snapshot(file_existence={"/tmp/payload.sh": True})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False

    def test_fallback_absent_from_permissions_and_contents(self):
        """Older audit.py without file_existence — file not in either collector means absent."""
        snap = _snapshot()  # file_existence = {}
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is True

    def test_fallback_present_in_permissions(self):
        snap = _snapshot(file_permissions={"/tmp/payload.sh": {"permissions": "755"}})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False

    def test_fallback_present_in_contents(self):
        snap = _snapshot(file_contents={"/tmp/payload.sh": "#!/bin/bash\nbad stuff"})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False


# ── file_hash_changed ─────────────────────────────────────────────────────────

class TestFileHashChanged:
    verification = {"type": "file_hash_changed", "path": "/etc/malware.conf"}
    original_hash = "abc123"
    changed_hash = "def456"

    def test_hash_changed(self):
        snap = _snapshot(file_hashes={"/etc/malware.conf": self.changed_hash})
        build_state = {"file_hashes": {"/etc/malware.conf": self.original_hash}}
        assert extract_and_check(self.verification, snap, STORED_FLAG, build_state) is True

    def test_hash_unchanged(self):
        snap = _snapshot(file_hashes={"/etc/malware.conf": self.original_hash})
        build_state = {"file_hashes": {"/etc/malware.conf": self.original_hash}}
        assert extract_and_check(self.verification, snap, STORED_FLAG, build_state) is False

    def test_missing_current_hash(self):
        """File was deleted (no hash from current run) — not the same as changed."""
        snap = _snapshot(file_hashes={"/etc/malware.conf": None})
        build_state = {"file_hashes": {"/etc/malware.conf": self.original_hash}}
        assert extract_and_check(self.verification, snap, STORED_FLAG, build_state) is False

    def test_missing_original_hash(self):
        """No build-time hash recorded — can't verify change."""
        snap = _snapshot(file_hashes={"/etc/malware.conf": self.changed_hash})
        build_state = {}
        assert extract_and_check(self.verification, snap, STORED_FLAG, build_state) is False

    def test_missing_both(self):
        snap = _snapshot()
        build_state = {}
        assert extract_and_check(self.verification, snap, STORED_FLAG, build_state) is False


# ── cron_not_present ──────────────────────────────────────────────────────────

class TestCronNotPresent:
    verification = {"type": "cron_not_present", "pattern": "malicious_task.sh"}

    def test_cron_removed(self):
        snap = _snapshot(cron_entries=["0 * * * * /usr/bin/apt-get update"])
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is True

    def test_cron_still_present(self):
        snap = _snapshot(cron_entries=[
            "*/5 * * * * /opt/malicious_task.sh",
            "0 * * * * /usr/bin/apt-get update",
        ])
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False

    def test_no_cron_entries_at_all(self):
        snap = _snapshot(cron_entries=[])
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is True

    def test_partial_match(self):
        """Pattern must appear as a substring anywhere in the cron line."""
        snap = _snapshot(cron_entries=["0 2 * * * /bin/bash /opt/malicious_task.sh --arg"])
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False


# ── user_not_exists ───────────────────────────────────────────────────────────

class TestUserNotExists:
    verification = {"type": "user_not_exists", "user": "backdoor"}

    def test_user_removed_from_both(self):
        snap = _snapshot(passwd_users=["root", "ubuntu"], shadow_hashes={"root": "!*"})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is True

    def test_user_still_in_passwd(self):
        snap = _snapshot(passwd_users=["root", "backdoor"], shadow_hashes={"root": "!*"})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False

    def test_user_still_in_shadow(self):
        snap = _snapshot(passwd_users=["root"], shadow_hashes={"root": "!*", "backdoor": "$6$abc"})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False

    def test_user_in_both(self):
        snap = _snapshot(
            passwd_users=["root", "backdoor"],
            shadow_hashes={"root": "!*", "backdoor": "$6$abc"},
        )
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is False

    def test_empty_system(self):
        snap = _snapshot(passwd_users=[], shadow_hashes={})
        assert extract_and_check(self.verification, snap, STORED_FLAG, {}) is True
