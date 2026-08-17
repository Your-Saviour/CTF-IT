# api/services/operation_driver.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from api.services.caldera import CalderaClient, get_caldera_api_key


@dataclass
class AbilityResult:
    status: int
    output: str
    finished: bool


class OperationDriver:
    def __init__(self, caldera: CalderaClient | None = None):
        self.caldera = caldera or CalderaClient(get_caldera_api_key())

    async def ensure_run_source(self, run_id: int) -> str:
        source_id = f"ctf-run-{run_id}"
        await self.caldera.ensure_source(source_id, name=f"ctf-run-{run_id}")
        return source_id

    async def seed_run_facts(self, source_id: str, fact_store: dict[str, str]) -> None:
        facts = [{"trait": trait, "value": value} for trait, value in fact_store.items()]
        if facts:
            await self.caldera.seed_facts(facts, source_id=source_id)

    async def resolve_agent_paw(self, ip_address: str) -> str | None:
        agent = await self.caldera.get_agent_by_ip(ip_address)
        return agent.get("paw") if agent else None

    async def execute(self, ability_id: str, adversary_id: str, agent_paw: str,
                      group: str, source_id: str, timeout_seconds: int) -> AbilityResult:
        planner = await self.caldera.get_planner_by_name("atomic")
        op = await self.caldera.create_operation(
            name=f"CTF step {ability_id[:8]}", adversary_id=adversary_id,
            planner_id=planner["id"], group=group, source_id=source_id,
            autonomous=True, state="running", allowed_agents=[agent_paw],
        )
        op_id = op["id"]
        deadline = time.monotonic() + timeout_seconds
        while True:
            detail = await self.caldera.get_operation(op_id, include_chain=True)
            if detail.get("state") in ("finished", "cleanup", "failed"):
                break
            if time.monotonic() > deadline:
                return AbilityResult(status=-1, output="timeout", finished=False)
            await asyncio.sleep(2)
        chain = detail.get("chain", [])
        link = chain[-1] if chain else {}
        return AbilityResult(
            status=link.get("status", -1),
            output=link.get("output", "") or "",
            finished=bool(link.get("finish")),
        )
