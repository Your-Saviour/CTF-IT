from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache

import httpx

from ai_agent.config import get_config

logger = logging.getLogger(__name__)

# Cache statistics
cache_stats = {
    "hits": 0,
    "misses": 0,
    "invalidations": 0,
}


def get_cache_stats() -> dict:
    """Get cache statistics."""
    total = cache_stats["hits"] + cache_stats["misses"]
    hit_rate = cache_stats["hits"] / total if total > 0 else 0
    return {
        **cache_stats,
        "hit_rate": hit_rate
    }


def invalidate_cache(pattern: str = None) -> None:
    """Invalidate cache entries matching pattern."""
    global cache_stats
    cache_stats["invalidations"] += 1
    logger.info(f"Cache invalidated: pattern={pattern or 'all'}")

# Sanitization patterns
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\. ]{1,100}$")
SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
SAFE_IP_PATTERN = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
SAFE_DOMAIN_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
SAFE_UUID_PATTERN = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")

# Valid target patterns
VALID_TARGET_PATTERNS = [SAFE_IP_PATTERN, SAFE_DOMAIN_PATTERN, SAFE_UUID_PATTERN]


def _sanitize_name(value: str, fallback: str = "unnamed") -> str:
    if not value or not SAFE_NAME_PATTERN.match(value):
        return fallback
    return value.strip()


def _sanitize_id(value: str) -> str | None:
    if not value or not SAFE_ID_PATTERN.match(value):
        return None
    return value

def validate_target(value: str) -> bool:
    """Validate target IP/domain format."""
    if not value:
        return False
    return any(pattern.match(value) for pattern in VALID_TARGET_PATTERNS)


class CalderaTool:
    """Tool for interacting with MITRE Caldera."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.config = get_config()
        self.api_key = self.config.get_caldera_api_key()
        self.client = httpx.AsyncClient(
            base_url=self.config.CALDERA_URL,
            headers={"KEY": self.api_key},
            timeout=60.0,
        )

    async def close(self):
        await self.client.aclose()

    async def execute(self, action: dict) -> str:
        action_type = action.get("action_type", "")

        if action_type == "caldera_operation":
            return await self._create_operation(action)
        elif action_type == "caldera_ability":
            return await self._execute_ability(action)
        else:
            return f"Unsupported Caldera action type: {action_type}"

    async def _get_operation_status(self, op_id: str) -> dict:
        """Get current status of an operation."""
        try:
            resp = await self.client.get(f"/api/v2/operations/{op_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            return {"error": str(e)}

    async def monitor_operation(self, op_id: str, timeout: int = 60) -> dict:
        """Monitor operation progress until completion, timeout, or health issues."""
        start_time = asyncio.get_event_loop().time()
        last_status = {}
        health_issues = []

        while asyncio.get_event_loop().time() - start_time < timeout:
            status = await self._get_operation_status(op_id)

            # Check for health issues
            if status.get("state") == "running":
                # Verify operation has at least one agent
                agents = status.get("agents", [])
                if not agents:
                    health_issues.append("No agents connected to operation")

                # Check if operation is stalled
                last_update = status.get("last_update")
                if last_update:
                    age = asyncio.get_event_loop().time() - last_update
                    if age > 30:
                        health_issues.append(f"Operation appears stalled (last update {age:.0f}s ago)")

                last_status = status

                # Log health issues
                if health_issues:
                    logger.warning(f"Operation {op_id} health checks: {', '.join(health_issues)}")

            elif status.get("state") in ("completed", "failed", "stopping"):
                return status
            else:
                last_status = status
                await asyncio.sleep(2)

        # Check final health on timeout
        final_status = await self._get_operation_status(op_id)
        if final_status.get("state") == "running":
            health_issues.append("Operation timed out while still running")

        result = {
            "error": f"Operation monitoring timed out after {timeout}s",
            "status": last_status,
            "health_issues": health_issues if health_issues else None
        }

        if health_issues:
            logger.warning(f"Operation {op_id} timeout with health issues: {', '.join(health_issues)}")

        return result

    async def check_operation_health(self, op_id: str) -> dict:
        """Check detailed health status of an operation."""
        try:
            resp = await self.client.get(f"/api/v2/operations/{op_id}")
            resp.raise_for_status()
            status = resp.json()

            health = {
                "op_id": op_id,
                "state": status.get("state"),
                "agents_connected": len(status.get("agents", [])),
                "agents_total": status.get("agent_count", 0),
                "abilities_executed": status.get("abilities_executed", 0),
                "abilities_total": status.get("abilities_total", 0),
                "last_update": status.get("last_update"),
                "health_score": self._calculate_health_score(status),
            }

            return health
        except httpx.HTTPError as e:
            return {"error": f"Failed to check operation health: {e}"}

    def _calculate_health_score(self, status: dict) -> float:
        """Calculate a health score for an operation (0-1)."""
        score = 1.0

        # Penalize for no agents
        agents_connected = status.get("agents", [])
        if not agents_connected:
            score -= 0.3
        else:
            # Penalize for disconnected agents
            total_agents = status.get("agent_count", 0)
            connected_count = len(agents_connected)
            if connected_count < total_agents:
                score -= (1 - connected_count / total_agents) * 0.2

        # Penalize for stalled operations
        last_update = status.get("last_update")
        if last_update:
            import time
            age = time.time() - last_update
            if age > 30:
                score -= 0.2

        # Reward for progress
        abilities_executed = status.get("abilities_executed", 0)
        abilities_total = status.get("abilities_total", 0)
        if abilities_total > 0:
            progress = abilities_executed / abilities_total
            score += progress * 0.3

        return max(0.0, min(1.0, score))

    async def _create_operation(self, action: dict) -> str:
        try:
            adversaries = await self._get_adversaries()
            adversary_name = _sanitize_name(action.get("adversary_name", ""), "CTF Full Exploit Chain")

            adversary = None
            for adv in adversaries:
                if adv.get("name") == adversary_name:
                    adversary = adv
                    break

            if not adversary:
                available = [a.get("name") for a in adversaries[:5]]
                return f"Adversary '{adversary_name}' not found. Available: {available}"

            group = _sanitize_name(action.get("group", ""), f"ai-agent-{self.session_id[:8]}")
            op_name = _sanitize_name(action.get("name", ""), f"AI Agent Op: {action.get('description', '')[:50]}")

            planners = await self._get_planners()
            planner_id = None
            for p in planners:
                if p.get("name") == "atomic":
                    planner_id = p.get("id")
                    break

            if not planner_id:
                return "Atomic planner not found"

            op_payload = {
                "name": op_name,
                "adversary_id": adversary.get("adversary_id"),
                "planner_id": planner_id,
                "group": group,
                "state": "running",
            }

            resp = await self.client.post("/api/v2/operations", json=op_payload)
            resp.raise_for_status()
            op = resp.json()

            op_id = op.get("id")
            return f"Operation created and started: {op_id}"

        except httpx.HTTPError as e:
            return f"Caldera API error: {e}"
        except Exception as e:
            return f"Error creating operation: {e}"

    async def _execute_ability(self, action: dict, max_retries: int | None = None) -> str:
        """Execute ability with exponential backoff retry logic."""
        if max_retries is None:
            max_retries = self.config.CALDERA_MAX_RETRIES

        op_id = _sanitize_id(action.get("operation_id"))
        if not op_id:
            return "Invalid or missing operation_id"

        ability_id = _sanitize_id(action.get("ability_id"))
        if not ability_id:
            return "Invalid or missing ability_id"

        # Verify operation exists
        try:
            resp = await self.client.get(f"/api/v2/operations/{op_id}")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Failed to verify operation {op_id}: {e}"

        for attempt in range(max_retries):
            try:
                # Queue ability
                await self._queue_ability(op_id, ability_id)

                # Wait for execution with timeout and exponential backoff
                timeout = 60
                for i in range(timeout):
                    status = await self._get_operation_status(op_id)
                    abilities = status.get("abilities", {})

                    if abilities.get(ability_id) == "completed":
                        return f"Ability {ability_id} executed successfully"
                    elif abilities.get(ability_id) == "failed":
                        if attempt < max_retries - 1:
                            # Exponential backoff between retries
                            delay = self.config.CALDERA_RETRY_DELAY * (self.config.CALDERA_RETRY_BACKOFF ** attempt)
                            logger.warning(f"Ability {ability_id} failed, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(delay)
                            break
                        else:
                            return f"Ability {ability_id} execution failed after {max_retries} attempts"

                    await asyncio.sleep(2)

                # Check if we should retry
                if attempt < max_retries - 1:
                    delay = self.config.CALDERA_RETRY_DELAY * (self.config.CALDERA_RETRY_BACKOFF ** attempt)
                    logger.warning(f"Ability {ability_id} timed out, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                    continue

                return f"Ability {ability_id} execution timed out after {max_retries} attempts"

            except httpx.HTTPError as e:
                if attempt < max_retries - 1:
                    delay = self.config.CALDERA_RETRY_DELAY * (self.config.CALDERA_RETRY_BACKOFF ** attempt)
                    logger.warning(f"Caldera API error for {ability_id}, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                return f"Caldera API error after {max_retries} attempts: {e}"
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = self.config.CALDERA_RETRY_DELAY * (self.config.CALDERA_RETRY_BACKOFF ** attempt)
                    logger.warning(f"Unexpected error executing {ability_id}, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                return f"Error executing ability after {max_retries} attempts: {e}"

    async def _queue_ability(self, op_id: str, ability_id: str) -> str:
        """Queue an ability for execution in an operation."""
        try:
            resp = await self.client.post(
                f"/api/v2/operations/{op_id}/abilities",
                json={"ability_id": ability_id}
            )
            resp.raise_for_status()
            return f"Ability {ability_id} queued for operation {op_id}"
        except httpx.HTTPError as e:
            return f"Failed to queue ability: {e}"

    _adversaries_cache: list[dict] | None = None
    _planners_cache: list[dict] | None = None

    async def _get_adversaries(self) -> list[dict]:
        """Get adversaries with caching."""
        global cache_stats
        if self._adversaries_cache is not None:
            cache_stats["hits"] += 1
            return self._adversaries_cache
        cache_stats["misses"] += 1
        resp = await self.client.get("/api/v2/adversaries")
        resp.raise_for_status()
        self._adversaries_cache = resp.json()
        return self._adversaries_cache

    async def _get_planners(self) -> list[dict]:
        """Get planners with caching."""
        global cache_stats
        if self._planners_cache is not None:
            cache_stats["hits"] += 1
            return self._planners_cache
        cache_stats["misses"] += 1
        resp = await self.client.get("/api/v2/planners")
        resp.raise_for_status()
        self._planners_cache = resp.json()
        return self._planners_cache

    def invalidate_adversary_cache(self) -> None:
        """Invalidate adversary cache."""
        global cache_stats
        self._get_adversaries.cache_clear()
        cache_stats["invalidations"] += 1
        logger.info("Adversary cache invalidated")

    def invalidate_planner_cache(self) -> None:
        """Invalidate planner cache."""
        global cache_stats
        self._get_planners.cache_clear()
        cache_stats["invalidations"] += 1
        logger.info("Planner cache invalidated")

    async def list_abilities(self) -> list[dict]:
        resp = await self.client.get("/api/v2/abilities")
        resp.raise_for_status()
        return resp.json()

    async def batch_execute_abilities(self, abilities: list[dict]) -> list[str]:
        """Execute multiple abilities in a single operation."""
        try:
            adversary = await self._get_adversaries()
            adversary_name = _sanitize_name(abilities[0].get("adversary_name", ""), "CTF Full Exploit Chain")

            selected_adversary = None
            for adv in adversary:
                if adv.get("name") == adversary_name:
                    selected_adversary = adv
                    break

            if not selected_adversary:
                return [f"Adversary '{adversary_name}' not found"]

            planner = await self._get_planners()
            planner_id = None
            for p in planner:
                if p.get("name") == "atomic":
                    planner_id = p.get("id")
                    break

            if not planner_id:
                return ["Atomic planner not found"]

            group = _sanitize_name(abilities[0].get("group", ""), f"ai-agent-{self.session_id[:8]}")
            op_name = _sanitize_name(abilities[0].get("name", ""), "Batch Operation")

            op_payload = {
                "name": op_name,
                "adversary_id": selected_adversary.get("adversary_id"),
                "planner_id": planner_id,
                "group": group,
                "state": "running",
            }

            resp = await self.client.post("/api/v2/operations", json=op_payload)
            resp.raise_for_status()
            op = resp.json()
            op_id = op.get("id")

            results = []
            for ability in abilities:
                result = await self._queue_ability(op_id, ability["id"])
                results.append(result)

            return results + [f"Operation {op_id} created and running"]

        except httpx.HTTPError as e:
            return [f"Caldera API error: {e}"]
        except Exception as e:
            return [f"Error creating batch operation: {e}"]

    async def resume_operation(self, op_id: str) -> str:
        """Resume a paused operation."""
        op_id = _sanitize_id(op_id)
        if not op_id:
            return "Invalid operation_id"
        try:
            resp = await self.client.patch(f"/api/v2/operations/{op_id}", json={"state": "running"})
            resp.raise_for_status()
            return f"Operation {op_id} resumed"
        except httpx.HTTPError as e:
            return f"Failed to resume operation: {e}"
