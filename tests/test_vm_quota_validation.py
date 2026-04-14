import pytest
from builder.vm_quota_validation import validate_vm_quota


class TestFirewallRole:
    def test_firewall_role_is_valid(self):
        errors = validate_vm_quota(
            {
                "fw": {
                    "base_type": "opnsense",
                    "count": 1,
                    "role": "firewall",
                    "default_plan": "vc2-2c-4gb",
                }
            },
            valid_base_ids={"opnsense"},
        )
        assert errors == []

    def test_target_role_still_valid(self):
        errors = validate_vm_quota(
            {"t": {"base_type": "ubuntu_24_server", "count": 2, "role": "target"}},
            valid_base_ids={"ubuntu_24_server"},
        )
        assert errors == []

    def test_attacker_role_still_valid(self):
        errors = validate_vm_quota(
            {"a": {"base_type": "ubuntu_24_server", "count": 1, "role": "attacker"}},
            valid_base_ids={"ubuntu_24_server"},
        )
        assert errors == []

    def test_unknown_role_rejected(self):
        errors = validate_vm_quota(
            {"bad": {"base_type": "ubuntu_24_server", "count": 1, "role": "gateway"}},
            valid_base_ids={"ubuntu_24_server"},
        )
        assert any("role" in e for e in errors)

    def test_mixed_quota_with_firewall_valid(self):
        errors = validate_vm_quota(
            {
                "fw": {"base_type": "opnsense", "count": 1, "role": "firewall"},
                "target": {"base_type": "ubuntu_24_server", "count": 3, "role": "target"},
                "attacker": {"base_type": "ubuntu_24_server", "count": 1, "role": "attacker"},
            },
            valid_base_ids={"opnsense", "ubuntu_24_server"},
        )
        assert errors == []
