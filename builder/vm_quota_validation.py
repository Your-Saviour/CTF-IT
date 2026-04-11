import re

VALID_ROLES = {"target", "attacker"}
ALLOWED_KEYS = {"os", "default_plan", "count", "role", "region"}
SLUG_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def validate_vm_quota(vm_quota: dict) -> list[str]:
    """Validate a vm_quota dict. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    if not isinstance(vm_quota, dict):
        return ["vm_quota must be a JSON object"]

    if not vm_quota:
        return ["vm_quota must have at least one VM type entry"]

    for key, spec in vm_quota.items():
        if not SLUG_RE.match(key):
            errors.append(f"VM type key '{key}' must be alphanumeric/underscores only")
            continue

        if not isinstance(spec, dict):
            errors.append(f"'{key}' must be an object")
            continue

        extra = set(spec.keys()) - ALLOWED_KEYS
        if extra:
            errors.append(f"'{key}' has unknown keys: {', '.join(sorted(extra))}")

        if "os" not in spec or not isinstance(spec.get("os"), str) or not spec["os"]:
            errors.append(f"'{key}.os' is required and must be a non-empty string")

        if "default_plan" not in spec or not isinstance(spec.get("default_plan"), str) or not spec["default_plan"]:
            errors.append(f"'{key}.default_plan' is required and must be a non-empty string")

        count = spec.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errors.append(f"'{key}.count' must be a positive integer")

        role = spec.get("role")
        if role not in VALID_ROLES:
            errors.append(f"'{key}.role' must be one of: {', '.join(sorted(VALID_ROLES))}")

        if "region" in spec and (not isinstance(spec["region"], str) or not spec["region"]):
            errors.append(f"'{key}.region' must be a non-empty string if provided")

    return errors
