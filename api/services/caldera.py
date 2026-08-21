"""Async MITRE Caldera REST API client.

Designed for use in FastAPI route handlers (async context).
"""
from __future__ import annotations

import json
import os

import httpx

CALDERA_INTERNAL_URL = os.environ.get("CALDERA_INTERNAL_URL", "http://ctf-caldera:8888")
CALDERA_CONFIG_PATH = os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")

# Dedicated source for CTF platform facts. Kept separate from stockpile's
# "basic" source so seeding known VM metadata never mutates plugin data.
CTF_SOURCE_ID = "cf9e0f7e-0000-4000-8000-000000000001"
_ATOMIC_PLANNER_NAME = "atomic"

# Caldera link status codes (c_link.Link.states). Operations created with
# ``autonomous=False`` produce links at PAUSE; approving sets them to EXECUTE,
# rejecting sets them to DISCARD.
LINK_STATUS_PAUSE = -1
LINK_STATUS_EXECUTE = -3
LINK_STATUS_DISCARD = -2

# Fact traits seeded into the operation source for every known VM so abilities
# can reference them without a recon step.
VM_FACT_HOSTNAME = "ctf.hostname"
VM_FACT_IP = "ctf.ip"
VM_FACT_OS = "ctf.os"
VM_FACT_HOST_ID = "host.id"


def vm_source_facts(vm) -> list[dict]:
    """Build source facts describing a VM's known metadata.

    Accepts any object exposing ``hostname``, ``ip_address``, ``os`` and ``id``
    (the ``VM`` model) and returns fact dicts suitable for ``seed_facts()``.
    Facts with empty/None values are skipped.
    """
    facts: list[dict] = []
    mapping = [
        (VM_FACT_HOSTNAME, getattr(vm, "hostname", None)),
        (VM_FACT_IP, getattr(vm, "ip_address", None)),
        (VM_FACT_OS, getattr(vm, "os", None)),
        (VM_FACT_HOST_ID, str(getattr(vm, "id", "")) or None),
    ]
    for trait, value in mapping:
        if value:
            facts.append({"trait": trait, "value": str(value)})
    return facts


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
            transport=httpx.AsyncHTTPTransport(retries=3),
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
        source_id: str = CTF_SOURCE_ID,
        auto_close: bool = True,
        autonomous: bool = True,
        state: str | None = None,
        obfuscator: str = "plain-text",
        jitter: str = "2/8",
        visibility: int = 50,
        allowed_agents: list[str] | None = None,
    ) -> dict:
        payload = {
            "name": name,
            "adversary": {"adversary_id": adversary_id},
            "planner": {"id": planner_id},
            "source": {"id": source_id},
            "group": group,
            "auto_close": auto_close,
            "autonomous": 1 if autonomous else 0,
            "obfuscator": obfuscator,
            "jitter": jitter,
            "visibility": visibility,
        }
        if allowed_agents:
            payload["allowed_agents"] = allowed_agents
        if state:
            payload["state"] = state
        resp = await self._client.post("/api/v2/operations", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def update_operation(self, op_id: str, **fields) -> dict:
        """PATCH an operation (e.g. state, autonomous, obfuscator, visibility).

        Accepts any OperationSchema field; ``autonomous`` is coerced to int.
        """
        data = dict(fields)
        if "autonomous" in data and isinstance(data["autonomous"], bool):
            data["autonomous"] = 1 if data["autonomous"] else 0
        resp = await self._client.patch(f"/api/v2/operations/{op_id}", json=data)
        resp.raise_for_status()
        return resp.json()

    async def update_operation_link(self, op_id: str, link_id: str, **fields) -> dict:
        """PATCH a link within an operation (e.g. status for approve/reject)."""
        resp = await self._client.patch(
            f"/api/v2/operations/{op_id}/links/{link_id}", json=fields
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_operation(self, op_id: str) -> None:
        resp = await self._client.delete(f"/api/v2/operations/{op_id}")
        resp.raise_for_status()

    async def get_operation_report(self, op_id: str, include_output: bool = False) -> dict:
        """Fetch a finished operation's debrief report (steps + facts + objectives)."""
        resp = await self._client.post(
            f"/api/v2/operations/{op_id}/report",
            json={"enable_agent_output": include_output},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_operation_event_logs(self, op_id: str, include_output: bool = False) -> dict:
        """Fetch a finished operation's event logs."""
        resp = await self._client.post(
            f"/api/v2/operations/{op_id}/event-logs",
            json={"enable_agent_output": include_output},
        )
        resp.raise_for_status()
        return resp.json()

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

    async def list_planners(self) -> list[dict]:
        resp = await self._client.get("/api/v2/planners")
        resp.raise_for_status()
        return resp.json()

    async def list_obfuscators(self) -> list[dict]:
        resp = await self._client.get("/api/v2/obfuscators")
        resp.raise_for_status()
        return resp.json()

    async def ensure_source(
        self, source_id: str = CTF_SOURCE_ID, name: str = "ctf"
    ) -> None:
        """Ensure the CTF fact source exists (created empty if missing)."""
        resp = await self._client.get(f"/api/v2/sources/{source_id}")
        if resp.status_code != 404:
            resp.raise_for_status()
            return
        create_resp = await self._client.post(
            "/api/v2/sources",
            json={"name": name, "id": source_id, "facts": [], "rules": [], "relationships": []},
        )
        create_resp.raise_for_status()

    async def seed_facts(
        self, facts: list[dict], source_id: str = CTF_SOURCE_ID, name: str = "ctf"
    ) -> None:
        """Add facts to the CTF operation source so abilities can use them without recon.

        ``facts`` is a list of ``{"trait": str, "value": str}`` dicts (typically
        known VM/event metadata like hostname, IP, OS). The dedicated CTF source
        is created if missing; existing facts are preserved so repeated calls are
        idempotent. Uses PUT (create-or-update) rather than PATCH because the
        PATCH path round-trips on-disk plugin facts and can 500.
        """
        resp = await self._client.get(f"/api/v2/sources/{source_id}")
        if resp.status_code == 404:
            existing = []
            rules = []
            relationships = []
            source_name = name
        else:
            resp.raise_for_status()
            source = resp.json()
            existing = [{"trait": f.get("trait"), "value": f.get("value")}
                        for f in source.get("facts", []) or []]
            rules = source.get("rules", []) or []
            relationships = source.get("relationships", []) or []
            source_name = source.get("name", name)
        existing_traits = {(f.get("trait"), f.get("value")) for f in existing}
        merged = list(existing)
        for fact in facts:
            key = (fact.get("trait"), fact.get("value"))
            if key not in existing_traits:
                merged.append(fact)
                existing_traits.add(key)
        put_resp = await self._client.put(
            f"/api/v2/sources/{source_id}",
            json={
                "id": source_id,
                "name": source_name,
                "facts": merged,
                "rules": rules,
                "relationships": relationships,
            },
        )
        put_resp.raise_for_status()

    async def get_atomic_planner_id(self) -> str:
        planner = await self.get_planner_by_name(_ATOMIC_PLANNER_NAME)
        return planner["id"]
