import random

from builder.module_loader import Module


def find_module(module_id: str, library: list[Module]) -> Module:
    for m in library:
        if m.id == module_id:
            return m
    raise ValueError(f"Module '{module_id}' not found in library")


def select_modules(quota: dict, module_library: list[Module]) -> list[Module]:
    selected: list[Module] = []

    for module_type, tiers in quota.items():
        pool = [m for m in module_library if m.type == module_type]

        for difficulty, count in tiers.items():
            tier_pool = [m for m in pool if m.difficulty == difficulty]

            for _ in range(count):
                selected_ids = {m.id for m in selected}
                conflicts = {c for m in selected for c in m.conflicts}
                available = [
                    m for m in tier_pool
                    if m.id not in selected_ids
                    and m.id not in conflicts
                ]

                if not available:
                    raise ValueError(
                        f"No available {difficulty} {module_type} modules"
                    )

                pick = random.choice(available)
                selected.append(pick)

                for req_id in pick.requires:
                    if req_id not in selected_ids:
                        req = find_module(req_id, module_library)
                        selected.append(req)

    return selected
