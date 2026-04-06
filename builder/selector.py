import random

from builder.module_loader import Module
from builder.quota_validation import RESERVED_KEYS


def find_module(module_id: str, library: list[Module]) -> Module:
    for m in library:
        if m.id == module_id:
            return m
    raise ValueError(f"Module '{module_id}' not found in library")


def _pick_available(pool: list[Module], selected: list[Module]) -> Module | None:
    """Pick a random module from pool that isn't already selected and doesn't conflict."""
    selected_ids = {m.id for m in selected}
    # Conflicts are bidirectional: exclude candidates listed in any selected
    # module's conflicts, AND candidates that list any selected module in
    # their own conflicts.
    blocked_by_selected = {c for m in selected for c in m.conflicts}
    available = [
        m for m in pool
        if m.id not in selected_ids
        and m.id not in blocked_by_selected
        and not (set(m.conflicts) & selected_ids)
    ]
    if not available:
        return None
    return random.choice(available)


def _pull_requires(pick: Module, selected: list[Module], library: list[Module]):
    """Add required modules before the pick so their scripts run first in the Dockerfile."""
    selected_ids = {m.id for m in selected}
    blocked_by_selected = {c for m in selected for c in m.conflicts}
    for req_id in pick.requires:
        if req_id not in selected_ids:
            req = find_module(req_id, library)
            if req.id in blocked_by_selected or (set(req.conflicts) & selected_ids):
                raise ValueError(
                    f"Module '{pick.id}' requires '{req_id}' which conflicts "
                    f"with already-selected modules"
                )
            selected.append(req)


def select_modules(quota: dict, module_library: list[Module]) -> list[Module]:
    selected: list[Module] = []

    # Phase 1: type → difficulty selection (existing behaviour)
    for module_type, tiers in quota.items():
        if module_type in RESERVED_KEYS:
            continue
        pool = [m for m in module_library if m.type == module_type]

        for difficulty, count in tiers.items():
            tier_pool = [m for m in pool if m.difficulty == difficulty]

            # Count modules already pulled in via dependency resolution
            already = sum(
                1 for m in selected
                if m.type == module_type and m.difficulty == difficulty
            )
            remaining = max(0, count - already)

            for _ in range(remaining):
                pick = _pick_available(tier_pool, selected)
                if pick is None:
                    raise ValueError(
                        f"No available {difficulty} {module_type} modules"
                    )
                _pull_requires(pick, selected, module_library)
                selected.append(pick)

    # Phase 2: category quotas
    for category, count in quota.get("categories", {}).items():
        already = sum(1 for m in selected if m.category == category)
        deficit = count - already
        if deficit <= 0:
            continue
        cat_pool = [m for m in module_library if m.category == category]
        for _ in range(deficit):
            pick = _pick_available(cat_pool, selected)
            if pick is None:
                raise ValueError(
                    f"Not enough modules in category '{category}' "
                    f"(wanted {count}, have {already})"
                )
            _pull_requires(pick, selected, module_library)
            selected.append(pick)

    # Phase 3: tag quotas
    for tag, count in quota.get("tags", {}).items():
        already = sum(1 for m in selected if tag in m.tags)
        deficit = count - already
        if deficit <= 0:
            continue
        tag_pool = [m for m in module_library if tag in m.tags]
        for _ in range(deficit):
            pick = _pick_available(tag_pool, selected)
            if pick is None:
                raise ValueError(
                    f"Not enough modules with tag '{tag}' "
                    f"(wanted {count}, have {already})"
                )
            _pull_requires(pick, selected, module_library)
            selected.append(pick)

    return selected
