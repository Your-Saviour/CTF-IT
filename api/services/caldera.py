"""Async MITRE Caldera REST API client.

Designed for use in FastAPI route handlers (async context).
"""
from __future__ import annotations

import json
import os

import httpx

CALDERA_INTERNAL_URL = os.environ.get("CALDERA_INTERNAL_URL", "http://ctf-caldera:8888")
CALDERA_CONFIG_PATH = os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")

_DEFAULT_SOURCE_ID = "ed32b9c3-9593-4c33-b0db-e2007315096b"
_ATOMIC_PLANNER_NAME = "atomic"


class CalderaError(Exception):
    """Raised when a Caldera API call fails."""


def get_caldera_api_key() -> str:
    """Read Caldera API key from local.yml. Returns empty string if file missing."""
    import yaml as _yaml

    if not os.path.exists(CALDERA_CONFIG_PATH):
        return ""
    with open(CALDERA_CONFIG_PATH) as f:
        config = _yaml.safe_load(f)
    return config.get("api_key_red", "")


class CalderaClient:
    """Async Caldera REST API client.

    Usage::

        async with CalderaClient() as client:
            ops = await client.list_operations()
    """

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or get_caldera_api_key()
        self._client = httpx.AsyncClient(
            base_url=CALDERA_INTERNAL_URL,
            headers={"KEY": self._api_key},
            timeout=30.0,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    async def aclose(self):
        await self._client.aclose()

    # ── Operations ────────────────────────────────────────────────────────────

    async def list_operations(self) -> list[dict]:
        resp = await self._client.get("/api/v2/operations")
        resp.raise_for_status()
        return resp.json()

    async def get_operation(self, op_id: str, include_chain: bool = False) -> dict:
        url = f"/api/v2/operations/{op_id}"
        if include_chain:
            url += "?include=chain"
        resp = await self._client.get(url)
        resp.raise_for_status()
        operation = resp.json()

        # Caldera 5.3 returns only ``{"chain": [...]}`` from the detail
        # endpoint, while the collection endpoint contains the operation
        # metadata.  Merge the two so callers receive a stable shape across
        # supported Caldera releases.
        if isinstance(operation, dict) and not operation.get("id"):
            for listed_operation in await self.list_operations():
                if listed_operation.get("id") == op_id:
                    return {**listed_operation, **operation}
        return operation

    async def create_operation(
        self,
        name: str,
        adversary_id: str,
        planner_id: str,
        group: str,
        source_id: str = _DEFAULT_SOURCE_ID,
        auto_close: bool = True,
    ) -> dict:
        payload = {
            "name": name,
            "adversary": {"adversary_id": adversary_id},
            "planner": {"id": planner_id},
            "source": {"id": source_id},
            "group": group,
            "auto_close": auto_close,
        }
        resp = await self._client.post("/api/v2/operations", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def delete_operation(self, op_id: str) -> None:
        resp = await self._client.delete(f"/api/v2/operations/{op_id}")
        resp.raise_for_status()

    # ── Agents ────────────────────────────────────────────────────────────────

    async def list_agents(self) -> list[dict]:
        resp = await self._client.get("/api/v2/agents")
        resp.raise_for_status()
        return resp.json()

    async def get_agent_by_ip(self, ip: str) -> dict | None:
        agents = await self.list_agents()
        for agent in agents:
            if ip in agent.get("host_ip_addrs", []):
                return agent
        return None

    # ── Abilities & Adversaries ───────────────────────────────────────────────

    async def list_abilities(self) -> list[dict]:
        resp = await self._client.get("/api/v2/abilities")
        resp.raise_for_status()
        return resp.json()

    async def list_adversaries(self) -> list[dict]:
        resp = await self._client.get("/api/v2/adversaries")
        resp.raise_for_status()
        return resp.json()

    async def get_adversary_by_name(self, name: str) -> dict | None:
        adversaries = await self.list_adversaries()
        return next((a for a in adversaries if a.get("name") == name), None)

    # ── Planners & Sources ────────────────────────────────────────────────────

    async def get_planner_by_name(self, name: str) -> dict:
        resp = await self._client.get("/api/v2/planners")
        resp.raise_for_status()
        for p in resp.json():
            if p.get("name") == name:
                return p
        raise CalderaError(f"No planner named '{name}' found in Caldera")

    async def ensure_source(
        self, source_id: str = _DEFAULT_SOURCE_ID, name: str = "basic"
    ) -> None:
        resp = await self._client.get("/api/v2/sources")
        resp.raise_for_status()
        if any(s.get("id") == source_id for s in resp.json()):
            return
        create_resp = await self._client.post(
            "/api/v2/sources",
            json={"name": name, "id": source_id, "facts": [], "rules": [], "relationships": []},
        )
        create_resp.raise_for_status()

    async def get_atomic_planner_id(self) -> str:
        planner = await self.get_planner_by_name(_ATOMIC_PLANNER_NAME)
        return planner["id"]
