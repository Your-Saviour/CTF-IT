import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from builder.module_loader import Module

logger = logging.getLogger(__name__)


def plan_for_vm(
    modules: list["Module"],
    default_plan: str,
    available_plans: list[dict],
) -> str:
    """Pick the cheapest Vultr plan that satisfies module resource requirements.

    ``available_plans`` entries must have keys: id, ram (MB), vcpu_count, monthly_cost.
    Returns the Vultr plan ID string.
    """
    if not available_plans:
        return default_plan

    plans_by_id = {p["id"]: p for p in available_plans}

    # Determine baseline from default plan
    default = plans_by_id.get(default_plan)
    if default:
        base_ram = default["ram"]
        base_vcpu = default["vcpu_count"]
    else:
        base_ram = 0
        base_vcpu = 0

    # Sum module resource requirements
    total_ram = sum(getattr(m, "min_ram_mb", 0) for m in modules)
    total_vcpu = sum(getattr(m, "min_vcpu", 0) for m in modules)

    required_ram = max(base_ram, total_ram)
    required_vcpu = max(base_vcpu, total_vcpu)

    # Find cheapest plan that fits
    candidates = [
        p for p in available_plans
        if p["ram"] >= required_ram and p["vcpu_count"] >= required_vcpu
    ]

    if candidates:
        best = min(candidates, key=lambda p: p["monthly_cost"])
        if best["id"] != default_plan:
            logger.info(
                "Upgraded plan from %s to %s (need %dMB RAM, %d vCPU)",
                default_plan, best["id"], required_ram, required_vcpu,
            )
        return best["id"]

    # Nothing fits — return largest by RAM
    fallback = max(available_plans, key=lambda p: p["ram"])
    logger.warning(
        "No plan meets requirements (%dMB RAM, %d vCPU). Using largest: %s",
        required_ram, required_vcpu, fallback["id"],
    )
    return fallback["id"]
