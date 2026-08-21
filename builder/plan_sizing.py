import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from builder.base_loader import BaseType
    from builder.module_loader import Module

logger = logging.getLogger(__name__)


def plan_for_vm(
    base_type: "BaseType",
    modules: list["Module"],
    vm_quota_override_plan: "str | None",
    available_plans: list[dict],
    *,
    region: str | None = None,
) -> str:
    """Pick the cheapest offered instance type satisfying resource requirements.

    ``base_type`` provides the baseline plan when no vm_quota override is given.
    ``vm_quota_override_plan`` is an optional per-entry override from the vm_quota JSON.
    AWS catalogue entries use instance_type, memory_mb, vcpu, hourly_cost, and
    regions. Legacy plan keys remain accepted during the provider cutover.
    """
    floor_plan = vm_quota_override_plan or base_type.default_plan

    if not available_plans:
        return floor_plan

    aws_catalogue = "instance_type" in available_plans[0]
    id_key = "instance_type" if aws_catalogue else "id"
    ram_key = "memory_mb" if aws_catalogue else "ram"
    vcpu_key = "vcpu" if aws_catalogue else "vcpu_count"
    cost_key = "hourly_cost" if aws_catalogue else "monthly_cost"
    offered_plans = [
        plan for plan in available_plans
        if not aws_catalogue or region is None or region in plan.get("regions", [])
    ]
    if not offered_plans:
        return floor_plan
    plans_by_id = {p[id_key]: p for p in offered_plans}

    # Determine baseline from floor plan
    default = plans_by_id.get(floor_plan)
    if default:
        base_ram = default[ram_key]
        base_vcpu = default[vcpu_key]
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
        p for p in offered_plans
        if p[ram_key] >= required_ram and p[vcpu_key] >= required_vcpu
    ]

    if candidates:
        best = min(candidates, key=lambda p: p[cost_key])
        if best[id_key] != floor_plan:
            logger.info(
                "Upgraded plan from %s to %s (need %dMB RAM, %d vCPU)",
                floor_plan, best[id_key], required_ram, required_vcpu,
            )
        return best[id_key]

    # Nothing fits — return largest by RAM
    fallback = max(offered_plans, key=lambda p: p[ram_key])
    logger.warning(
        "No plan meets requirements (%dMB RAM, %d vCPU). Using largest: %s",
        required_ram, required_vcpu, fallback[id_key],
    )
    return fallback[id_key]
