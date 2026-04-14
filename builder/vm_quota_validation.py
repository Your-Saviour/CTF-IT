import re

VALID_ROLES = {"target", "attacker", "firewall"}
ALLOWED_KEYS = {"base_type", "default_plan", "count", "role", "region"}
SLUG_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def validate_vm_quota(vm_quota: dict, valid_base_ids: set[str]) -> list[str]:
    """Validate a vm_quota dict. Returns list of error strings (empty = valid).

    Args:
        vm_quota: The VM quota dict to validate.
        valid_base_ids: Set of IDs from non-disabled base types (used to validate base_type values).
    """
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

        base_type = spec.get("base_type")
        if not isinstance(base_type, str) or not base_type:
            errors.append(f"'{key}.base_type' is required and must be a non-empty string")
        elif not SLUG_RE.match(base_type):
            errors.append(f"'{key}.base_type' must match pattern ^[a-zA-Z0-9_]+$")
        elif base_type not in valid_base_ids:
            errors.append(
                f"'{key}.base_type' references unknown or disabled base type '{base_type}'"
            )

        if "default_plan" in spec:
            if not isinstance(spec["default_plan"], str) or not spec["default_plan"]:
                errors.append(f"'{key}.default_plan' must be a non-empty string if provided")

        count = spec.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errors.append(f"'{key}.count' must be a positive integer")

        role = spec.get("role")
        if role not in VALID_ROLES:
            errors.append(f"'{key}.role' must be one of: {', '.join(sorted(VALID_ROLES))}")

        if "region" in spec and (not isinstance(spec["region"], str) or not spec["region"]):
            errors.append(f"'{key}.region' must be a non-empty string if provided")

    return errors
