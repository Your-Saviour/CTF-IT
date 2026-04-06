import pytest

from builder.module_loader import Module
from builder.quota_validation import validate_quota
from builder.selector import select_modules


def _mod(id, type="vulnerability", difficulty="easy", category="general",
         tags=None, conflicts=None, requires=None):
    """Helper to create test modules."""
    return Module(
        id=id, name=id, description="", type=type, difficulty=difficulty,
        points=100, category=category, tags=tags or [], conflicts=conflicts or [],
        requires=requires or [],
    )


# ── Fixtures ──

@pytest.fixture
def library():
    return [
        _mod("v_auth_e1", category="authentication"),
        _mod("v_auth_e2", category="authentication"),
        _mod("v_fs_e1", category="filesystem"),
        _mod("v_fs_m1", difficulty="medium", category="filesystem"),
        _mod("v_net_m1", difficulty="medium", category="network", tags=["ssh"]),
        _mod("h_auth_e1", type="hardening", category="authentication", tags=["ssh"]),
        _mod("h_auth_m1", type="hardening", difficulty="medium", category="authentication"),
        _mod("h_svc_m1", type="hardening", difficulty="medium", category="services"),
        _mod("a_web_e1", type="application", category="web", tags=["flask"]),
    ]


# ── Phase 1: Backward compatibility ──

class TestTypeDifficultySelection:
    def test_basic_quota(self, library):
        quota = {"vulnerability": {"easy": 1, "medium": 0, "hard": 0}}
        result = select_modules(quota, library)
        assert len(result) == 1
        assert result[0].type == "vulnerability"
        assert result[0].difficulty == "easy"

    def test_multi_type(self, library):
        quota = {
            "vulnerability": {"easy": 1, "medium": 0, "hard": 0},
            "hardening": {"easy": 0, "medium": 1, "hard": 0},
        }
        result = select_modules(quota, library)
        assert len(result) == 2
        types = {m.type for m in result}
        assert types == {"vulnerability", "hardening"}

    def test_application_type(self, library):
        quota = {"application": {"easy": 1}}
        result = select_modules(quota, library)
        assert len(result) == 1
        assert result[0].type == "application"

    def test_insufficient_modules_raises(self, library):
        quota = {"vulnerability": {"hard": 1}}
        with pytest.raises(ValueError, match="No available hard vulnerability"):
            select_modules(quota, library)

    def test_reserved_keys_skipped(self, library):
        quota = {"vulnerability": {"easy": 1}, "categories": {}, "tags": {}}
        result = select_modules(quota, library)
        assert len(result) == 1


# ── Phase 2: Category quotas ──

class TestCategorySelection:
    def test_category_already_satisfied(self, library):
        """If type quota already picked enough from a category, no extras added."""
        quota = {
            "vulnerability": {"easy": 2, "medium": 0, "hard": 0},
            "categories": {"authentication": 1},
        }
        result = select_modules(quota, library)
        # Type picked 2 easy vulns. Auth has 2 easy vulns, so likely picked some.
        # Category wants 1 auth — should be satisfied already.
        assert len(result) == 2

    def test_category_adds_deficit(self, library):
        """Category quota picks additional modules if type quota didn't satisfy it."""
        quota = {
            "vulnerability": {"easy": 1, "medium": 0, "hard": 0},
            "categories": {"authentication": 3},
        }
        result = select_modules(quota, library)
        auth_count = sum(1 for m in result if m.category == "authentication")
        assert auth_count >= 3

    def test_category_across_types(self, library):
        """Category quota can pick from any type."""
        quota = {"categories": {"authentication": 3}}
        result = select_modules(quota, library)
        assert len(result) == 3
        types = {m.type for m in result}
        # Should include both vuln and hardening auth modules
        assert len(types) > 1

    def test_category_insufficient_raises(self, library):
        quota = {"categories": {"web": 5}}
        with pytest.raises(ValueError, match="Not enough modules in category 'web'"):
            select_modules(quota, library)


# ── Phase 3: Tag quotas ──

class TestTagSelection:
    def test_tag_quota(self, library):
        quota = {"tags": {"ssh": 2}}
        result = select_modules(quota, library)
        ssh_count = sum(1 for m in result if "ssh" in m.tags)
        assert ssh_count >= 2

    def test_tag_already_satisfied(self, library):
        """If type/category picks already have enough tagged modules, no extras."""
        quota = {
            "hardening": {"easy": 1, "medium": 0, "hard": 0},
            "tags": {"ssh": 1},
        }
        result = select_modules(quota, library)
        # h_auth_e1 is the only easy hardening and has ssh tag
        assert len(result) == 1
        assert "ssh" in result[0].tags

    def test_tag_insufficient_raises(self, library):
        quota = {"tags": {"nonexistent": 1}}
        with pytest.raises(ValueError, match="Not enough modules with tag 'nonexistent'"):
            select_modules(quota, library)


# ── Conflicts & Dependencies ──

class TestConflictsAndDeps:
    def test_conflicts_respected_across_phases(self):
        lib = [
            _mod("a", category="cat1", conflicts=["b"]),
            _mod("b", category="cat1"),
            _mod("c", category="cat1"),
        ]
        quota = {
            "vulnerability": {"easy": 1},
            "categories": {"cat1": 2},
        }
        # Run many times to check conflict is always respected
        for _ in range(20):
            result = select_modules(quota, lib)
            ids = {m.id for m in result}
            assert not ({"a", "b"} <= ids), "a and b should not both be selected"

    def test_requires_pulled_in(self):
        lib = [
            _mod("app", type="application", category="web"),
            _mod("vuln", category="web", requires=["app"]),
        ]
        quota = {"vulnerability": {"easy": 1}}
        result = select_modules(quota, lib)
        ids = [m.id for m in result]
        assert "vuln" in ids
        assert "app" in ids
        # Dependency should come before the pick
        assert ids.index("app") < ids.index("vuln")

    def test_requires_counted_toward_type_quota(self):
        lib = [
            _mod("app", type="application", category="web"),
            _mod("app2", type="application", category="web"),
            _mod("vuln", category="web", requires=["app"]),
        ]
        quota = {
            "vulnerability": {"easy": 1},
            "application": {"easy": 1},
        }
        result = select_modules(quota, lib)
        app_count = sum(1 for m in result if m.type == "application")
        # app was pulled by requires, should count toward application quota
        # so the application tier shouldn't add a second one
        assert app_count == 1


# ── Quota Validation ──

class TestValidation:
    def test_valid_basic_quota(self):
        assert validate_quota({
            "vulnerability": {"easy": 1, "medium": 0, "hard": 0}
        }) == []

    def test_valid_full_quota(self):
        assert validate_quota({
            "vulnerability": {"easy": 1},
            "hardening": {"medium": 1},
            "application": {"easy": 1},
            "categories": {"auth": 2},
            "tags": {"ssh": 1},
        }) == []

    def test_invalid_not_dict(self):
        errors = validate_quota("not a dict")
        assert len(errors) == 1

    def test_invalid_difficulty(self):
        errors = validate_quota({"vulnerability": {"extreme": 1}})
        assert any("Unknown difficulty" in e for e in errors)

    def test_invalid_count_negative(self):
        errors = validate_quota({"vulnerability": {"easy": -1}})
        assert any("non-negative" in e for e in errors)

    def test_invalid_category_count(self):
        errors = validate_quota({"categories": {"auth": "two"}})
        assert any("non-negative" in e for e in errors)

    def test_invalid_tags_not_dict(self):
        errors = validate_quota({"tags": "ssh"})
        assert any("must be an object" in e for e in errors)
