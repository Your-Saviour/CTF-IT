VALID_DIFFICULTIES = {"easy", "medium", "hard"}
RESERVED_KEYS = {"categories", "tags"}


def validate_quota(quota: dict) -> list[str]:
    """Validate a quota dict. Returns list of error strings (empty = valid)."""
    errors = []
    if not isinstance(quota, dict):
        return ["Quota must be a JSON object"]

    for key, value in quota.items():
        if key in RESERVED_KEYS:
            if not isinstance(value, dict):
                errors.append(f"'{key}' must be an object")
                continue
            for k, v in value.items():
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errors.append(f"'{key}.{k}' must be a non-negative integer")
        else:
            # Treat as module type → difficulty mapping
            if not isinstance(value, dict):
                errors.append(f"'{key}' must be an object of difficulty -> count")
                continue
            for diff, count in value.items():
                if diff not in VALID_DIFFICULTIES:
                    errors.append(f"Unknown difficulty '{diff}' in '{key}'")
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    errors.append(f"'{key}.{diff}' must be a non-negative integer")

    return errors
