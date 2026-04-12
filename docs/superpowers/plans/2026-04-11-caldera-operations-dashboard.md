# Caldera Operations Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operations management layer, results dashboard, and red team status view to the existing Caldera integration, giving instructors visibility into attack progress and per-VM vulnerability status.

**Architecture:** A new `CalderaClient` service centralizes all Caldera API calls; `caldera_ops.py` exposes new REST endpoints; a new `caldera_dashboard.html` template provides the UI. All data is fetched on demand from Caldera's API — nothing is persisted locally.

**Tech Stack:** FastAPI, httpx (async), Jinja2, SQLAlchemy, existing `api/services/semaphore.py` pattern

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `api/services/caldera.py` | **Create** | Reusable async Caldera API client |
| `api/routes/caldera_setup.py` | **Modify** | Refactor to use CalderaClient |
| `api/routes/vm.py` | **Modify** | Agent group: `"red"` → `"event-{event_id}"` |
| `api/routes/caldera_ops.py` | **Create** | Operation CRUD + results endpoints |
| `api/main.py` | **Modify** | Register caldera_ops router + page routes |
| `frontend/templates/caldera_dashboard.html` | **Create** | Operations list + detail pages |
| `frontend/templates/vm_detail.html` | **Modify** | Red Team Status card |
| `frontend/templates/admin.html` | **Modify** | Red Team nav link |
| `builder/caldera.py` | **Modify** | Expose `ability_uuid` for module→ability reverse lookup |

---

## Task 1: Create CalderaClient service

**Files:**
- Create: `api/services/caldera.py`

- [ ] **Step 1: Write the file**

```python
# api/services/caldera.py
"""Async MITRE Caldera REST API client.

Designed for use in FastAPI route handlers (async context).
"""
from __future__ import annotations

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
        return resp.json()

    async def create_operation(
        self,
        name: str,
        adversary_id: str,
        planner_id: str,
        group: str,
        source_id: str = _DEFAULT_SOURCE_ID,
        auto_close: bool = False,
    ) -> dict:
        import json as _json
        payload = {
            "name": name,
            "adversary": {"adversary_id": adversary_id},
            "planner": {"id": planner_id},
            "source": {"id": source_id},
            "group": group,
            "auto_close": auto_close,
        }
        resp = await self._client.post(
            "/api/v2/operations",
            content=_json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
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
        import json as _json
        create_resp = await self._client.post(
            "/api/v2/sources",
            content=_json.dumps(
                {"name": name, "id": source_id, "facts": [], "rules": [], "relationships": []}
            ),
            headers={"Content-Type": "application/json"},
        )
        create_resp.raise_for_status()

    async def get_atomic_planner_id(self) -> str:
        planner = await self.get_planner_by_name(_ATOMIC_PLANNER_NAME)
        return planner["id"]
```

- [ ] **Step 2: Verify file is importable**

```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
python -c "from api.services.caldera import CalderaClient, get_caldera_api_key; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/services/caldera.py
git commit -m "feat: add CalderaClient service for async Caldera API access"
```

---

## Task 2: Expose ability_uuid in builder/caldera.py

**Files:**
- Modify: `builder/caldera.py`

The `_ability_uuid` function is currently private (underscore-prefixed). The results endpoint needs to reverse-map Caldera ability UUIDs back to CTF module IDs. We expose a public version and add a convenience function that returns the full UUID→module mapping.

- [ ] **Step 1: Add public `ability_uuid` and `build_ability_uuid_map` to `builder/caldera.py`**

After line 23 (`return str(uuid.uuid5(_NAMESPACE, f"{module_id}_{phase}"))`), add:

```python
def ability_uuid(module_id: str, phase: str) -> str:
    """Public alias for deterministic ability UUID generation.

    Returns the same UUID that generate_caldera_export() uses for abilities,
    enabling reverse-lookup of which CTF module an operation result belongs to.
    """
    return _ability_uuid(module_id, phase)


def build_ability_uuid_map(modules: list) -> dict[str, dict]:
    """Return a mapping of ability_uuid -> {module_id, module_name, phase}.

    Used by the operations results endpoint to annotate Caldera link results
    with the corresponding CTF module name.
    """
    result = {}
    for m in modules:
        if m.type != "vulnerability" or not m.caldera:
            continue
        cal = m.caldera
        if cal.get("recon", {}).get("command"):
            result[_ability_uuid(m.id, "recon")] = {
                "module_id": m.id,
                "module_name": m.name,
                "phase": "recon",
            }
        if cal.get("exploit", {}).get("command"):
            result[_ability_uuid(m.id, "exploit")] = {
                "module_id": m.id,
                "module_name": m.name,
                "phase": "exploit",
            }
    return result
```

- [ ] **Step 2: Verify importable**

```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
python -c "from builder.caldera import ability_uuid, build_ability_uuid_map; print(ability_uuid('suid_find', 'recon'))"
```

Expected output: a UUID string like `a7f3c2e1-...`

- [ ] **Step 3: Commit**

```bash
git add builder/caldera.py
git commit -m "feat: expose ability_uuid and build_ability_uuid_map for results reverse-lookup"
```

---

## Task 3: Refactor caldera_setup.py to use CalderaClient

**Files:**
- Modify: `api/routes/caldera_setup.py`

Replace the inline `httpx.AsyncClient` helpers (`_get_ctf_abilities`, `_get_adversaries`, `_create_operation`, `_ensure_basic_source`, `_get_atomic_planner_id`, `_wait_for_caldera`) with `CalderaClient`. The `_load_caldera_config`, `_ensure_plugin_in_config`, `_write_caldera_config`, and `_copy_plugin_files` helpers stay as-is (they deal with the filesystem, not the API).

- [ ] **Step 1: Replace the file content**

The full updated file:

```python
# api/routes/caldera_setup.py
import asyncio
import json
import os
import shutil
import uuid

import docker
import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key

router = APIRouter(prefix="/admin", tags=["admin"])

CALDERA_PLUGIN_DIR = os.environ.get("CALDERA_PLUGIN_DIR", "/caldera-plugin/ctf-exploit")
CALDERA_CONFIG_PATH = os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")
CALDERA_CONTAINER_NAME = os.environ.get("CALDERA_CONTAINER_NAME", "ctf-caldera")
CALDERA_STARTUP_TIMEOUT = int(os.environ.get("CALDERA_STARTUP_TIMEOUT", "120"))
CALDERA_INTERNAL_URL = os.environ.get("CALDERA_INTERNAL_URL", "http://ctf-caldera:8888")


def _load_caldera_config() -> dict:
    """Read and parse Caldera local.yml, raising ValueError if missing or invalid."""
    if not os.path.exists(CALDERA_CONFIG_PATH):
        raise ValueError(
            f"Caldera config not found at {CALDERA_CONFIG_PATH}. "
            "Ensure the API container has CALDERA_CONFIG_PATH mounted."
        )
    with open(CALDERA_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _ensure_plugin_in_config(config: dict) -> bool:
    """Add ctf-exploit to the plugins list if not already present. Returns True if modified."""
    plugins = config.setdefault("plugins", [])
    if "ctf-exploit" not in plugins:
        plugins.append("ctf-exploit")
        return True
    return False


def _write_caldera_config(config: dict) -> None:
    with open(CALDERA_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def _copy_plugin_files(export_plugin_dir) -> int:
    """Copy exported plugin contents to the shared mount. Returns file count."""
    dest = CALDERA_PLUGIN_DIR
    if os.path.exists(dest):
        for item in os.listdir(dest):
            item_path = os.path.join(dest, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
    else:
        os.makedirs(dest)
    for item in os.listdir(str(export_plugin_dir)):
        src = os.path.join(str(export_plugin_dir), item)
        dst = os.path.join(dest, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return sum(1 for _ in export_plugin_dir.rglob("*") if _.is_file())


async def _wait_for_caldera(api_key: str, timeout: int = CALDERA_STARTUP_TIMEOUT) -> None:
    """Poll Caldera's health endpoint until it responds, or raise TimeoutError."""
    import httpx as _httpx
    deadline = asyncio.get_event_loop().time() + timeout
    async with _httpx.AsyncClient(timeout=10.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(CALDERA_INTERNAL_URL, headers={"KEY": api_key})
                if resp.status_code < 500:
                    return
            except (_httpx.ConnectError, _httpx.ReadTimeout):
                pass
            await asyncio.sleep(5)
    raise TimeoutError(f"Caldera did not become healthy within {timeout}s")


@router.post("/caldera-setup")
async def caldera_setup(request: Request, db: Session = Depends(get_db)):
    """
    Automated Caldera setup: generates the CTF exploit plugin, installs it,
    restarts Caldera, and creates an adversary operation.
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()

    if "event_id" in body:
        event = db.query(Event).filter(Event.id == body["event_id"]).first()
        if not event:
            return JSONResponse({"error": "Event not found"}, status_code=404)
        quota = json.loads(event.quota)
    elif "quota" in body:
        from builder.quota_validation import validate_quota
        errors = validate_quota(body["quota"])
        if errors:
            return JSONResponse({"error": "Invalid quota", "details": errors}, status_code=422)
        quota = body["quota"]
    else:
        return JSONResponse({"error": "Provide 'quota' or 'event_id'"}, status_code=400)

    # Step 1: Load and validate Caldera config
    try:
        config = _load_caldera_config()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    api_key = config.get("api_key_red")
    if not api_key or api_key == "REPLACE_ME":
        return JSONResponse(
            {"error": "api_key_red not set in Caldera config. Update local.yml first."},
            status_code=400,
        )

    # Step 2: Generate export
    export_id = f"caldera_{uuid.uuid4().hex[:12]}"
    from builder.caldera import generate_caldera_export
    try:
        output_dir = generate_caldera_export(quota, export_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    export_plugin_dir = output_dir / "plugins" / "ctf-exploit"

    try:
        # Step 3: Copy plugin files to shared mount
        file_count = _copy_plugin_files(export_plugin_dir)

        # Step 4: Update local.yml to include ctf-exploit
        modified = _ensure_plugin_in_config(config)
        if modified:
            _write_caldera_config(config)

        # Step 5: Restart Caldera container
        try:
            docker_client = docker.from_env()
            caldera_container = docker_client.containers.get(CALDERA_CONTAINER_NAME)
            caldera_container.restart()
        except docker.errors.NotFound:
            return JSONResponse(
                {"error": f"Caldera container '{CALDERA_CONTAINER_NAME}' not found."},
                status_code=400,
            )
        except docker.errors.DockerException as e:
            return JSONResponse({"error": f"Docker error: {e}"}, status_code=500)

        # Step 6: Wait for Caldera to be healthy
        try:
            await _wait_for_caldera(api_key)
        except TimeoutError as e:
            return JSONResponse({"error": str(e)}, status_code=504)

        async with CalderaClient(api_key) as caldera:
            # Step 7: Verify abilities loaded
            try:
                all_abilities = await caldera.list_abilities()
                ctf_abilities = [
                    a for a in all_abilities
                    if "Recon:" in a.get("name", "") or "Exploit:" in a.get("name", "")
                ]
            except Exception as e:
                return JSONResponse({"error": f"Failed to list abilities: {e}"}, status_code=502)

            # Step 8: Find "CTF Full Exploit Chain" adversary
            try:
                ctf_adversary = await caldera.get_adversary_by_name("CTF Full Exploit Chain")
            except Exception as e:
                return JSONResponse({"error": f"Failed to list adversaries: {e}"}, status_code=502)

            if not ctf_adversary:
                return JSONResponse(
                    {"error": "CTF Full Exploit Chain adversary not found after restart"},
                    status_code=500,
                )

            # Step 9: Ensure basic fact source exists
            try:
                await caldera.ensure_source()
            except Exception as e:
                return JSONResponse({"error": f"Failed to ensure basic source: {e}"}, status_code=502)

            # Step 10: Get atomic planner ID
            try:
                planner_id = await caldera.get_atomic_planner_id()
            except Exception as e:
                return JSONResponse({"error": f"Failed to locate atomic planner: {e}"}, status_code=502)

            # Step 11: Create operation
            operation_result = None
            operation_error = None
            try:
                operation_result = await caldera.create_operation(
                    name="CTF Red Team Emulation",
                    adversary_id=ctf_adversary["adversary_id"],
                    planner_id=planner_id,
                    group="red",
                )
            except Exception as e:
                operation_error = str(e)

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    response = {
        "status": "success" if operation_result else "partial",
        "plugin": {
            "files_copied": file_count,
            "plugin_added_to_config": modified,
            "abilities_loaded": len(ctf_abilities),
        },
    }
    if operation_result:
        response["operation"] = {
            "id": operation_result.get("id"),
            "name": operation_result.get("name"),
            "state": operation_result.get("state"),
        }
    if operation_error:
        response["operation_error"] = operation_error

    return JSONResponse(response)
```

- [ ] **Step 2: Verify the app starts without import errors**

```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
python -c "from api.routes.caldera_setup import router; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/routes/caldera_setup.py
git commit -m "refactor: caldera_setup uses CalderaClient instead of inline httpx"
```

---

## Task 4: Fix agent group targeting

**Files:**
- Modify: `api/routes/vm.py` (line 749)

The Sandcat agent deployment currently hardcodes `caldera_group = "red"`. Change it to use `event-{event_id}` so per-event operations can target the right agents. `vm.event_id` is already denormalized on the VM model.

- [ ] **Step 1: Update `_run_deploy_agent` in `api/routes/vm.py`**

Find line 749:
```python
        caldera_group = "red"
```

Replace with:
```python
        caldera_group = f"event-{vm.event_id}" if vm.event_id else "red"
```

- [ ] **Step 2: Also update the duplicate import of `_get_caldera_api_key` in `vm.py` to use the service**

Find (around line 856):
```python
def _get_caldera_api_key() -> str:
    """Read Caldera API key from local.yml config file."""
    import yaml as _yaml

    config_path = os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")
    if not os.path.exists(config_path):
        return ""
    with open(config_path) as f:
        config = _yaml.safe_load(f)
    return config.get("api_key_red", "")
```

Replace with:
```python
def _get_caldera_api_key() -> str:
    """Read Caldera API key from local.yml config file."""
    from api.services.caldera import get_caldera_api_key as _get_key
    return _get_key()
```

- [ ] **Step 3: Verify import**

```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
python -c "from api.routes.vm import router; print('OK')"
```

Expected output: `OK`

- [ ] **Step 4: Commit**

```bash
git add api/routes/vm.py
git commit -m "fix: sandcat agent group uses event-{id} for per-event operation targeting"
```

---

## Task 5: Create caldera_ops.py endpoints

**Files:**
- Create: `api/routes/caldera_ops.py`

This router provides:
- `GET /admin/caldera/operations` — list all operations with agent counts
- `POST /admin/caldera/operations` — create operation (per-event or per-VM)
- `GET /admin/caldera/operations/{op_id}` — detail with per-agent results mapped to modules
- `DELETE /admin/caldera/operations/{op_id}` — delete operation

- [ ] **Step 1: Write the file**

```python
# api/routes/caldera_ops.py
"""Caldera operation management API endpoints."""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event, Team, VM, VMModule
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key

router = APIRouter(prefix="/admin/caldera", tags=["admin"])


def _make_client() -> CalderaClient:
    return CalderaClient(get_caldera_api_key())


# ── Operations List ────────────────────────────────────────────────────────────

@router.get("/operations")
async def list_operations(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    # Build IP→VM lookup for hostname resolution
    all_vms = db.query(VM).all()
    ip_to_vm = {vm.ip_address: vm for vm in all_vms if vm.ip_address}

    result = []
    for op in operations:
        chain = op.get("chain", [])
        result.append({
            "id": op.get("id"),
            "name": op.get("name"),
            "state": op.get("state"),
            "group": op.get("group"),
            "start": op.get("start"),
            "finish": op.get("finish"),
            "abilities_run": len(chain),
        })
    return result


# ── Create Operation ───────────────────────────────────────────────────────────

@router.post("/operations")
async def create_operation(request: Request, db: Session = Depends(get_db)):
    """Create a Caldera operation scoped to an event or a specific VM.

    Body (one of):
      {"event_id": N, "adversary_name": "..."}   → targets all agents in event-N group
      {"vm_id": N, "adversary_name": "..."}       → targets the agent on that VM
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    adversary_name = body.get("adversary_name", "CTF Full Exploit Chain")

    async with _make_client() as caldera:
        # Ensure basic source exists
        try:
            await caldera.ensure_source()
        except Exception as e:
            return JSONResponse({"error": f"Could not ensure fact source: {e}"}, status_code=502)

        # Get planner
        try:
            planner_id = await caldera.get_atomic_planner_id()
        except Exception as e:
            return JSONResponse({"error": f"Could not find atomic planner: {e}"}, status_code=502)

        # Resolve adversary
        try:
            adversary = await caldera.get_adversary_by_name(adversary_name)
        except Exception as e:
            return JSONResponse({"error": f"Could not list adversaries: {e}"}, status_code=502)
        if not adversary:
            return JSONResponse({"error": f"Adversary '{adversary_name}' not found in Caldera"}, status_code=404)

        if "event_id" in body:
            event_id = body["event_id"]
            event = db.query(Event).filter(Event.id == event_id).first()
            if not event:
                return JSONResponse({"error": "Event not found"}, status_code=404)
            group = f"event-{event_id}"
            op_name = f"CTF Event {event.name} — {adversary_name}"

        elif "vm_id" in body:
            vm_id = body["vm_id"]
            vm = db.query(VM).filter(VM.id == vm_id).first()
            if not vm:
                return JSONResponse({"error": "VM not found"}, status_code=404)
            if not vm.ip_address:
                return JSONResponse({"error": "VM has no IP address"}, status_code=422)

            # Target the agent for this specific VM via its group
            group = f"event-{vm.event_id}" if vm.event_id else "red"
            op_name = f"CTF VM {vm.hostname or vm_id} — {adversary_name}"

        else:
            return JSONResponse({"error": "Provide 'event_id' or 'vm_id'"}, status_code=400)

        try:
            operation = await caldera.create_operation(
                name=op_name,
                adversary_id=adversary["adversary_id"],
                planner_id=planner_id,
                group=group,
            )
        except Exception as e:
            return JSONResponse({"error": f"Failed to create operation: {e}"}, status_code=502)

    return {
        "id": operation.get("id"),
        "name": operation.get("name"),
        "state": operation.get("state"),
        "group": operation.get("group"),
    }


# ── Operation Detail ───────────────────────────────────────────────────────────

@router.get("/operations/{op_id}")
async def get_operation(op_id: str, request: Request, db: Session = Depends(get_db)):
    """Return operation detail with per-agent results mapped to CTF module names."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            op = await caldera.get_operation(op_id, include_chain=True)
            agents = await caldera.list_agents()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    # Build ability UUID → module info mapping
    from builder.caldera import build_ability_uuid_map
    from builder.module_loader import load_all_modules
    modules = load_all_modules()
    uuid_to_module = build_ability_uuid_map(modules)

    # Build agent paw → VM hostname mapping
    all_vms = db.query(VM).all()
    ip_to_vm = {vm.ip_address: vm for vm in all_vms if vm.ip_address}
    paw_to_vm: dict[str, dict] = {}
    for agent in agents:
        paw = agent.get("paw")
        for ip in agent.get("host_ip_addrs", []):
            if ip in ip_to_vm:
                vm = ip_to_vm[ip]
                paw_to_vm[paw] = {"hostname": vm.hostname, "vm_id": vm.id, "ip": ip}
                break

    # Annotate chain links
    chain = op.get("chain", [])
    annotated_chain = []
    for link in chain:
        ability_id = link.get("ability", {}).get("ability_id", "")
        module_info = uuid_to_module.get(ability_id, {})
        agent_paw = link.get("paw", "")
        vm_info = paw_to_vm.get(agent_paw, {})
        annotated_chain.append({
            "id": link.get("id"),
            "paw": agent_paw,
            "vm_hostname": vm_info.get("hostname", agent_paw),
            "vm_id": vm_info.get("vm_id"),
            "ability_id": ability_id,
            "ability_name": link.get("ability", {}).get("name", ""),
            "tactic": link.get("ability", {}).get("tactic", ""),
            "technique_id": link.get("ability", {}).get("technique_id", ""),
            "module_id": module_info.get("module_id"),
            "module_name": module_info.get("module_name"),
            "phase": module_info.get("phase"),
            "status": link.get("status"),  # -3=timeout, -2=discarded, -1=fail, 0=queued, 1=success
            "output": (link.get("output") or "")[:500],  # truncate long outputs
            "collect": link.get("collect"),
            "finish": link.get("finish"),
        })

    # Per-agent summary
    agent_summary: dict[str, dict] = {}
    for link in annotated_chain:
        paw = link["paw"]
        if paw not in agent_summary:
            agent_summary[paw] = {
                "paw": paw,
                "vm_hostname": link["vm_hostname"],
                "vm_id": link["vm_id"],
                "success": 0,
                "failed": 0,
                "pending": 0,
            }
        s = link["status"]
        if s == 1:
            agent_summary[paw]["success"] += 1
        elif s in (-1, -3):
            agent_summary[paw]["failed"] += 1
        else:
            agent_summary[paw]["pending"] += 1

    return {
        "id": op.get("id"),
        "name": op.get("name"),
        "state": op.get("state"),
        "group": op.get("group"),
        "start": op.get("start"),
        "finish": op.get("finish"),
        "adversary": op.get("adversary", {}).get("name"),
        "chain": annotated_chain,
        "agents": list(agent_summary.values()),
    }


# ── Delete Operation ───────────────────────────────────────────────────────────

@router.delete("/operations/{op_id}")
async def delete_operation(op_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            await caldera.delete_operation(op_id)
        except Exception as e:
            return JSONResponse({"error": f"Failed to delete operation: {e}"}, status_code=502)

    return {"status": "deleted", "id": op_id}


# ── Red Team VM Summary ────────────────────────────────────────────────────────

@router.get("/vm-summary")
async def vm_attack_summary(request: Request, db: Session = Depends(get_db)):
    """Return per-VM attack summary across all operations.

    For each VM with a connected agent, shows total exploits attempted,
    succeeded, and failed — aggregated across all finished operations.
    """
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
            agents = await caldera.list_agents()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    all_vms = db.query(VM).all()
    ip_to_vm = {vm.ip_address: vm for vm in all_vms if vm.ip_address}

    # Map paw → VM
    paw_to_vm: dict[str, dict] = {}
    for agent in agents:
        paw = agent.get("paw")
        for ip in agent.get("host_ip_addrs", []):
            if ip in ip_to_vm:
                vm = ip_to_vm[ip]
                team = db.query(Team).filter(Team.id == vm.team_id).first()
                paw_to_vm[paw] = {
                    "hostname": vm.hostname,
                    "vm_id": vm.id,
                    "team_name": team.name if team else None,
                    "ip": ip,
                }
                break

    # Aggregate results
    vm_stats: dict[int, dict] = {}
    for op in operations:
        for link in op.get("chain", []):
            paw = link.get("paw", "")
            vm_info = paw_to_vm.get(paw)
            if not vm_info:
                continue
            vm_id = vm_info["vm_id"]
            if vm_id not in vm_stats:
                vm_stats[vm_id] = {
                    "vm_id": vm_id,
                    "hostname": vm_info["hostname"],
                    "team_name": vm_info["team_name"],
                    "total_attacks": 0,
                    "exploits_succeeded": 0,
                    "exploits_failed": 0,
                    "last_seen": None,
                }
            vm_stats[vm_id]["total_attacks"] += 1
            s = link.get("status")
            if s == 1:
                vm_stats[vm_id]["exploits_succeeded"] += 1
            elif s in (-1, -3):
                vm_stats[vm_id]["exploits_failed"] += 1
            finish = link.get("finish")
            if finish and (not vm_stats[vm_id]["last_seen"] or finish > vm_stats[vm_id]["last_seen"]):
                vm_stats[vm_id]["last_seen"] = finish

    return list(vm_stats.values())


# ── VM-specific operation results ─────────────────────────────────────────────

@router.get("/vm/{vm_id}/results")
async def vm_results(vm_id: int, request: Request, db: Session = Depends(get_db)):
    """Return the most recent operation results for a specific VM."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        return JSONResponse({"error": "VM not found"}, status_code=404)
    if not vm.ip_address:
        return JSONResponse({"results": [], "operation_id": None})

    async with _make_client() as caldera:
        try:
            operations = await caldera.list_operations()
            agents = await caldera.list_agents()
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    # Find this VM's agent paw
    vm_paw = None
    for agent in agents:
        if vm.ip_address in agent.get("host_ip_addrs", []):
            vm_paw = agent.get("paw")
            break

    if not vm_paw:
        return {"results": [], "operation_id": None, "message": "No agent found for this VM"}

    # Find the most recent finished operation that ran against this VM's group
    vm_group = f"event-{vm.event_id}" if vm.event_id else "red"
    relevant_ops = [
        op for op in operations
        if op.get("group") == vm_group and op.get("state") in ("finished", "cleanup", "running")
    ]
    if not relevant_ops:
        return {"results": [], "operation_id": None}

    # Sort by start time descending, pick most recent
    relevant_ops.sort(key=lambda o: o.get("start") or "", reverse=True)

    # Fetch the most recent operation with chain
    async with _make_client() as caldera:
        try:
            op = await caldera.get_operation(relevant_ops[0]["id"], include_chain=True)
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    from builder.caldera import build_ability_uuid_map
    from builder.module_loader import load_all_modules
    uuid_to_module = build_ability_uuid_map(load_all_modules())

    results = []
    for link in op.get("chain", []):
        if link.get("paw") != vm_paw:
            continue
        ability_id = link.get("ability", {}).get("ability_id", "")
        module_info = uuid_to_module.get(ability_id, {})
        results.append({
            "ability_name": link.get("ability", {}).get("name", ""),
            "module_id": module_info.get("module_id"),
            "module_name": module_info.get("module_name"),
            "phase": module_info.get("phase"),
            "status": link.get("status"),
            "finish": link.get("finish"),
        })

    return {
        "results": results,
        "operation_id": op.get("id"),
        "operation_name": op.get("name"),
    }
```

- [ ] **Step 2: Verify imports**

```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
python -c "from api.routes.caldera_ops import router; print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/routes/caldera_ops.py
git commit -m "feat: add caldera_ops router with operation CRUD and per-VM results endpoints"
```

---

## Task 6: Register router and page routes

**Files:**
- Modify: `api/main.py`

Add the `caldera_ops` router and two new page routes: the operations dashboard and operation detail view.

- [ ] **Step 1: Update imports in `api/main.py`**

Find:
```python
from api.routes import admin, ansible_export, auth, caldera_export, caldera_setup, images, scoreboard, verify, vm
```

Replace with:
```python
from api.routes import admin, ansible_export, auth, caldera_export, caldera_ops, caldera_setup, images, scoreboard, verify, vm
```

- [ ] **Step 2: Register the router in `api/main.py`**

Find:
```python
app.include_router(caldera_setup.router)
app.include_router(vm.router)
```

Replace with:
```python
app.include_router(caldera_setup.router)
app.include_router(caldera_ops.router)
app.include_router(vm.router)
```

- [ ] **Step 3: Add page routes in `api/main.py`**

After the existing `@app.get("/admin/vm/{vm_id}")` route (around line 213), add:

```python
@app.get("/admin/caldera", response_class=HTMLResponse)
async def caldera_dashboard_page(request: Request, db: Session = Depends(get_db)):
    from api.routes.auth import get_current_user
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "caldera_dashboard.html", {"request": request})


@app.get("/admin/caldera/operation/{op_id}", response_class=HTMLResponse)
async def caldera_operation_page(op_id: str, request: Request, db: Session = Depends(get_db)):
    from api.routes.auth import get_current_user
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "caldera_dashboard.html", {
        "request": request,
        "op_id": op_id,
    })
```

- [ ] **Step 4: Verify the app starts**

```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
python -c "from api.main import app; print('OK')"
```

Expected output: `OK`

- [ ] **Step 5: Commit**

```bash
git add api/main.py
git commit -m "feat: register caldera_ops router and add caldera dashboard page routes"
```

---

## Task 7: Create caldera_dashboard.html

**Files:**
- Create: `frontend/templates/caldera_dashboard.html`

This is a single template that renders two views: the operations list (when no `op_id`) and the operation detail (when `op_id` is set). Look at `frontend/templates/vm_detail.html` for the base layout pattern (dark theme, existing nav).

- [ ] **Step 1: Read the base layout pattern**

```bash
head -40 /Users/jaketownsend/Desktop/Projectsz/CTF-IT/frontend/templates/vm_detail.html
```

- [ ] **Step 2: Create the template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Red Team Operations — CTF-IT</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #0d1117; color: #c9d1d9; }
    .card { background: #161b22; border: 1px solid #30363d; }
    .card-header { background: #1c2128; border-bottom: 1px solid #30363d; }
    .table { color: #c9d1d9; }
    .table-dark { --bs-table-bg: #161b22; }
    .badge-success { background: #238636; }
    .badge-fail { background: #da3633; }
    .badge-pending { background: #6e7681; }
    .badge-running { background: #1f6feb; }
    .status-success { color: #3fb950; }
    .status-fail { color: #f85149; }
    .status-pending { color: #8b949e; }
    .output-pre { background: #0d1117; border: 1px solid #30363d; padding: 8px; font-size: 0.8em; max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }
    a { color: #58a6ff; }
    a:hover { color: #79c0ff; }
  </style>
</head>
<body>
<div class="container-fluid py-4">
  <div class="d-flex justify-content-between align-items-center mb-4">
    <div>
      <a href="/admin" class="text-decoration-none text-secondary me-3">← Admin</a>
      <h2 class="d-inline mb-0">Red Team Operations</h2>
    </div>
    <button class="btn btn-danger" onclick="showCreateModal()">+ Create Operation</button>
  </div>

  <!-- Operations list view (shown when no op_id in URL) -->
  <div id="ops-list-view">
    <div class="card mb-4">
      <div class="card-header d-flex justify-content-between align-items-center">
        <span>Operations</span>
        <span class="text-secondary" id="last-refresh" style="font-size:0.85em;"></span>
      </div>
      <div class="card-body p-0">
        <table class="table table-dark table-hover mb-0">
          <thead>
            <tr>
              <th>Name</th>
              <th>State</th>
              <th>Group</th>
              <th>Abilities Run</th>
              <th>Started</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="ops-tbody">
            <tr><td colspan="6" class="text-center text-secondary py-4">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- VM Attack Summary -->
    <div class="card">
      <div class="card-header">VM Red Team Status</div>
      <div class="card-body p-0">
        <table class="table table-dark table-hover mb-0">
          <thead>
            <tr>
              <th>VM</th>
              <th>Team</th>
              <th>Total Attacks</th>
              <th>Exploits Succeeded</th>
              <th>Exploits Failed</th>
              <th>Last Activity</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="vm-summary-tbody">
            <tr><td colspan="7" class="text-center text-secondary py-4">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Operation detail view (shown when op_id is in URL or selected) -->
  <div id="ops-detail-view" style="display:none">
    <div id="op-detail-content">Loading operation...</div>
  </div>
</div>

<!-- Create Operation Modal -->
<div class="modal fade" id="createOpModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content bg-dark text-light border-secondary">
      <div class="modal-header border-secondary">
        <h5 class="modal-title">Create Operation</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="mb-3">
          <label class="form-label">Scope</label>
          <select class="form-select bg-dark text-light border-secondary" id="op-scope" onchange="toggleScopeFields()">
            <option value="event">Per Event</option>
            <option value="vm">Per VM</option>
          </select>
        </div>
        <div id="event-scope-field" class="mb-3">
          <label class="form-label">Event ID</label>
          <input type="number" class="form-control bg-dark text-light border-secondary" id="op-event-id" placeholder="e.g. 1">
        </div>
        <div id="vm-scope-field" class="mb-3" style="display:none">
          <label class="form-label">VM ID</label>
          <input type="number" class="form-control bg-dark text-light border-secondary" id="op-vm-id" placeholder="e.g. 5">
        </div>
        <div class="mb-3">
          <label class="form-label">Adversary</label>
          <input type="text" class="form-control bg-dark text-light border-secondary" id="op-adversary" value="CTF Full Exploit Chain">
        </div>
        <div id="create-op-error" class="text-danger" style="display:none"></div>
      </div>
      <div class="modal-footer border-secondary">
        <button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button class="btn btn-danger" onclick="createOperation()">Create</button>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
  // Determine initial view from URL
  const urlParts = window.location.pathname.split('/');
  const opIdFromUrl = urlParts.includes('operation') ? urlParts[urlParts.indexOf('operation') + 1] : null;

  let refreshInterval = null;

  function stateColor(state) {
    if (state === 'finished') return 'success';
    if (state === 'running') return 'primary';
    if (state === 'cleanup') return 'warning';
    return 'secondary';
  }

  function statusLabel(status) {
    if (status === 1) return '<span class="status-success">✓ Success</span>';
    if (status === -1 || status === -3) return '<span class="status-fail">✗ Failed</span>';
    return '<span class="status-pending">⏳ Pending</span>';
  }

  async function loadOperations() {
    const resp = await fetch('/admin/caldera/operations');
    if (!resp.ok) {
      document.getElementById('ops-tbody').innerHTML =
        `<tr><td colspan="6" class="text-danger text-center py-4">Caldera unavailable</td></tr>`;
      return [];
    }
    const ops = await resp.json();
    const tbody = document.getElementById('ops-tbody');
    if (ops.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="text-center text-secondary py-4">No operations yet</td></tr>';
      return ops;
    }
    tbody.innerHTML = ops.map(op => `
      <tr>
        <td><a href="#" onclick="viewOperation('${op.id}'); return false">${op.name}</a></td>
        <td><span class="badge bg-${stateColor(op.state)}">${op.state}</span></td>
        <td><code>${op.group || '—'}</code></td>
        <td>${op.abilities_run}</td>
        <td>${op.start ? new Date(op.start * 1000).toLocaleString() : '—'}</td>
        <td>
          <button class="btn btn-sm btn-outline-danger" onclick="deleteOperation('${op.id}')">Delete</button>
        </td>
      </tr>
    `).join('');
    document.getElementById('last-refresh').textContent = 'Updated ' + new Date().toLocaleTimeString();
    return ops;
  }

  async function loadVmSummary() {
    const resp = await fetch('/admin/caldera/vm-summary');
    if (!resp.ok) return;
    const vms = await resp.json();
    const tbody = document.getElementById('vm-summary-tbody');
    if (vms.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-secondary py-4">No attack data yet</td></tr>';
      return;
    }
    tbody.innerHTML = vms.map(vm => {
      const allFailed = vm.exploits_succeeded === 0 && vm.total_attacks > 0;
      const anySucceeded = vm.exploits_succeeded > 0;
      const statusBadge = anySucceeded
        ? '<span class="badge bg-danger">Vulnerable</span>'
        : allFailed
          ? '<span class="badge bg-success">Defended</span>'
          : '<span class="badge bg-secondary">Unknown</span>';
      return `
        <tr>
          <td><a href="/admin/vm/${vm.vm_id}">${vm.hostname || vm.vm_id}</a></td>
          <td>${vm.team_name || '—'}</td>
          <td>${vm.total_attacks}</td>
          <td class="status-fail">${vm.exploits_succeeded}</td>
          <td class="status-success">${vm.exploits_failed}</td>
          <td>${vm.last_seen ? new Date(vm.last_seen * 1000).toLocaleString() : '—'}</td>
          <td>${statusBadge}</td>
        </tr>
      `;
    }).join('');
  }

  async function viewOperation(opId) {
    document.getElementById('ops-list-view').style.display = 'none';
    document.getElementById('ops-detail-view').style.display = 'block';
    window.history.pushState({}, '', `/admin/caldera/operation/${opId}`);
    document.getElementById('op-detail-content').innerHTML = 'Loading...';

    const resp = await fetch(`/admin/caldera/operations/${opId}`);
    if (!resp.ok) {
      document.getElementById('op-detail-content').innerHTML = '<div class="text-danger">Failed to load operation</div>';
      return;
    }
    const op = await resp.json();

    const chainRows = op.chain.map(link => `
      <tr>
        <td>${link.vm_hostname || link.paw}</td>
        <td>${link.module_name || link.ability_name}</td>
        <td><span class="badge bg-secondary">${link.tactic || '—'}</span></td>
        <td><span class="badge bg-info text-dark">${link.phase || '—'}</span></td>
        <td>${statusLabel(link.status)}</td>
        <td>${link.finish ? new Date(link.finish * 1000).toLocaleString() : '—'}</td>
        <td>${link.output ? `<pre class="output-pre mb-0">${escHtml(link.output)}</pre>` : '—'}</td>
      </tr>
    `).join('');

    const agentSummaryRows = op.agents.map(a => `
      <tr>
        <td>${a.vm_hostname || a.paw}</td>
        <td class="status-success">${a.success}</td>
        <td class="status-fail">${a.failed}</td>
        <td class="status-pending">${a.pending}</td>
      </tr>
    `).join('');

    document.getElementById('op-detail-content').innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
          <a href="/admin/caldera" class="text-decoration-none text-secondary me-3">← All Operations</a>
          <h4 class="d-inline mb-0">${op.name}</h4>
          <span class="badge bg-${stateColor(op.state)} ms-2">${op.state}</span>
        </div>
      </div>
      <div class="row mb-4">
        <div class="col-md-3"><div class="card text-center p-3">
          <div class="text-secondary small">Adversary</div>
          <div>${op.adversary || '—'}</div>
        </div></div>
        <div class="col-md-3"><div class="card text-center p-3">
          <div class="text-secondary small">Group</div>
          <code>${op.group || '—'}</code>
        </div></div>
        <div class="col-md-3"><div class="card text-center p-3">
          <div class="text-secondary small">Started</div>
          <div>${op.start ? new Date(op.start * 1000).toLocaleString() : '—'}</div>
        </div></div>
        <div class="col-md-3"><div class="card text-center p-3">
          <div class="text-secondary small">Finished</div>
          <div>${op.finish ? new Date(op.finish * 1000).toLocaleString() : '—'}</div>
        </div></div>
      </div>
      <div class="card mb-4">
        <div class="card-header">Agent Summary</div>
        <div class="card-body p-0">
          <table class="table table-dark mb-0">
            <thead><tr><th>VM</th><th class="status-success">Succeeded</th><th class="status-fail">Failed</th><th class="status-pending">Pending</th></tr></thead>
            <tbody>${agentSummaryRows || '<tr><td colspan="4" class="text-center text-secondary">No agents</td></tr>'}</tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="card-header">Ability Results</div>
        <div class="card-body p-0">
          <table class="table table-dark mb-0">
            <thead><tr><th>VM</th><th>Module</th><th>Tactic</th><th>Phase</th><th>Result</th><th>Time</th><th>Output</th></tr></thead>
            <tbody>${chainRows || '<tr><td colspan="7" class="text-center text-secondary">No results yet</td></tr>'}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  async function deleteOperation(opId) {
    if (!confirm('Delete this operation?')) return;
    await fetch(`/admin/caldera/operations/${opId}`, { method: 'DELETE' });
    loadOperations();
  }

  function showCreateModal() {
    new bootstrap.Modal(document.getElementById('createOpModal')).show();
  }

  function toggleScopeFields() {
    const scope = document.getElementById('op-scope').value;
    document.getElementById('event-scope-field').style.display = scope === 'event' ? '' : 'none';
    document.getElementById('vm-scope-field').style.display = scope === 'vm' ? '' : 'none';
  }

  async function createOperation() {
    const scope = document.getElementById('op-scope').value;
    const adversary = document.getElementById('op-adversary').value;
    const errEl = document.getElementById('create-op-error');
    errEl.style.display = 'none';

    const body = { adversary_name: adversary };
    if (scope === 'event') {
      const v = parseInt(document.getElementById('op-event-id').value);
      if (!v) { errEl.textContent = 'Enter an event ID'; errEl.style.display = ''; return; }
      body.event_id = v;
    } else {
      const v = parseInt(document.getElementById('op-vm-id').value);
      if (!v) { errEl.textContent = 'Enter a VM ID'; errEl.style.display = ''; return; }
      body.vm_id = v;
    }

    const resp = await fetch('/admin/caldera/operations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      errEl.textContent = data.error || 'Failed to create operation';
      errEl.style.display = '';
      return;
    }
    bootstrap.Modal.getInstance(document.getElementById('createOpModal')).hide();
    await loadOperations();
    viewOperation(data.id);
  }

  async function autoRefresh() {
    const ops = await loadOperations();
    const hasRunning = ops.some(op => op.state === 'running');
    clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
      loadOperations().then(ops => {
        if (!ops.some(o => o.state === 'running')) clearInterval(refreshInterval);
      });
    }, hasRunning ? 5000 : 30000);
  }

  // Initial load
  if (opIdFromUrl) {
    document.getElementById('ops-list-view').style.display = 'none';
    document.getElementById('ops-detail-view').style.display = 'block';
    viewOperation(opIdFromUrl);
  } else {
    autoRefresh();
    loadVmSummary();
  }
</script>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/templates/caldera_dashboard.html
git commit -m "feat: add Caldera operations dashboard template"
```

---

## Task 8: Add Red Team Status card to vm_detail.html

**Files:**
- Modify: `frontend/templates/vm_detail.html`

Add a "Red Team Status" card that shows the most recent Caldera operation results for this VM, and a "Run Attack" button.

- [ ] **Step 1: Read the bottom of the existing vm_detail.html to find the right insertion point**

```bash
grep -n "card\|module\|caldera\|agent" /Users/jaketownsend/Desktop/Projectsz/CTF-IT/frontend/templates/vm_detail.html | tail -30
```

- [ ] **Step 2: Add the Red Team Status card after the existing module progress card**

Find the closing `</div>` of the module progress card section (look for the card with "Module Progress" or "Modules" heading). Add after it:

```html
<!-- Red Team Status -->
<div class="card mt-4" id="red-team-card">
  <div class="card-header d-flex justify-content-between align-items-center">
    <span>Red Team Status</span>
    <button class="btn btn-sm btn-outline-danger" onclick="runAttack()">Run Attack</button>
  </div>
  <div class="card-body p-0" id="red-team-body">
    <div class="text-center text-secondary py-3">Loading...</div>
  </div>
</div>

<script>
  // vm_id is available in the page URL: /admin/vm/{vm_id}
  const vmId = window.location.pathname.split('/').pop();

  async function loadRedTeamStatus() {
    const resp = await fetch(`/admin/caldera/vm/${vmId}/results`);
    const data = await resp.json();
    const container = document.getElementById('red-team-body');

    if (!resp.ok) {
      container.innerHTML = `<div class="text-secondary text-center py-3">Caldera unavailable</div>`;
      return;
    }
    if (data.message) {
      container.innerHTML = `<div class="text-secondary text-center py-3">${data.message}</div>`;
      return;
    }
    if (!data.results || data.results.length === 0) {
      container.innerHTML = `<div class="text-secondary text-center py-3">No attack results yet. Run an operation to see results.</div>`;
      return;
    }

    const rows = data.results.map(r => {
      const statusIcon = r.status === 1
        ? '<span style="color:#f85149">✗ Exploited</span>'
        : r.status === -1 || r.status === -3
          ? '<span style="color:#3fb950">✓ Defended</span>'
          : '<span style="color:#8b949e">⏳ Pending</span>';
      return `
        <tr>
          <td>${r.module_name || r.ability_name}</td>
          <td><span class="badge bg-secondary">${r.phase || '—'}</span></td>
          <td>${statusIcon}</td>
          <td>${r.finish ? new Date(r.finish * 1000).toLocaleString() : '—'}</td>
        </tr>
      `;
    }).join('');

    const opLink = data.operation_id
      ? `<a href="/admin/caldera/operation/${data.operation_id}" class="text-decoration-none" style="font-size:0.85em">View full operation →</a>`
      : '';

    container.innerHTML = `
      <div class="px-3 pt-2 pb-1 d-flex justify-content-between align-items-center">
        <span class="text-secondary" style="font-size:0.85em">Operation: ${data.operation_name || data.operation_id || '—'}</span>
        ${opLink}
      </div>
      <table class="table table-dark mb-0">
        <thead><tr><th>Module</th><th>Phase</th><th>Result</th><th>Time</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  async function runAttack() {
    const btn = document.querySelector('#red-team-card button');
    btn.disabled = true;
    btn.textContent = 'Creating...';
    const resp = await fetch('/admin/caldera/operations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vm_id: parseInt(vmId) }),
    });
    const data = await resp.json();
    btn.disabled = false;
    btn.textContent = 'Run Attack';
    if (!resp.ok) {
      alert(data.error || 'Failed to create operation');
      return;
    }
    // Poll for results after a short delay
    setTimeout(loadRedTeamStatus, 3000);
  }

  loadRedTeamStatus();
</script>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/templates/vm_detail.html
git commit -m "feat: add Red Team Status card to VM detail page"
```

---

## Task 9: Add Red Team link to admin.html

**Files:**
- Modify: `frontend/templates/admin.html`

Add a "Red Team" navigation link in the Service Links section of the admin page.

- [ ] **Step 1: Find the service links section**

```bash
grep -n "caldera\|Service Links\|service-links\|Semaphore\|Registry" /Users/jaketownsend/Desktop/Projectsz/CTF-IT/frontend/templates/admin.html | head -20
```

- [ ] **Step 2: Add the Red Team Operations link**

Find the Caldera service link in the admin template (it links to the Caldera web UI via `caldera.${DOMAIN}`). Add a new link to the CTF-IT Red Team Operations page directly below it:

```html
<a href="/admin/caldera" class="btn btn-outline-danger btn-sm">Red Team Operations</a>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/templates/admin.html
git commit -m "feat: add Red Team Operations link to admin page"
```

---

## Verification Checklist

After implementation, verify end-to-end in the running docker-compose stack:

- [ ] `python -c "from api.services.caldera import CalderaClient; print('OK')"` — service imports cleanly
- [ ] `python -c "from builder.caldera import ability_uuid, build_ability_uuid_map; print('OK')"` — builder exports cleanly
- [ ] `python -c "from api.main import app; print('OK')"` — full app imports without errors
- [ ] `POST /admin/caldera-setup` (existing endpoint) still works with same response format
- [ ] Deploy an agent to a VM → check Caldera UI → agent group should be `event-{N}` not `red`
- [ ] `GET /admin/caldera/operations` returns list of operations
- [ ] `POST /admin/caldera/operations` with `{"event_id": 1}` creates operation targeting `event-1` group
- [ ] `GET /admin/caldera/operations/{id}` returns chain with `module_name` populated for CTF modules
- [ ] Navigate to `/admin/caldera` — dashboard loads, operations table renders
- [ ] Navigate to `/admin/caldera/operation/{id}` — detail page shows per-agent results
- [ ] Navigate to `/admin/vm/{id}` — Red Team Status card appears, shows results after running an operation
- [ ] Admin page has "Red Team Operations" link
