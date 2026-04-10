import asyncio
import json
import os
import shutil
import uuid

import docker
import httpx
import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Event
from api.routes.admin import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])

CALDERA_PLUGIN_DIR = os.environ.get("CALDERA_PLUGIN_DIR", "/caldera-plugin/ctf-exploit")
CALDERA_CONFIG_PATH = os.environ.get("CALDERA_CONFIG_PATH", "/caldera-config/local.yml")
CALDERA_INTERNAL_URL = os.environ.get("CALDERA_INTERNAL_URL", "http://ctf-caldera:8888")
CALDERA_CONTAINER_NAME = os.environ.get("CALDERA_CONTAINER_NAME", "ctf-caldera")
CALDERA_STARTUP_TIMEOUT = int(os.environ.get("CALDERA_STARTUP_TIMEOUT", "120"))

# Caldera's atomic planner and default source UUIDs
_ATOMIC_PLANNER_ID = "788f0f7e-96f0-4545-a0d1-0c587db5f2ee"
_DEFAULT_SOURCE_ID = "ed32b9c3-9593-4c33-b0db-e2007315096b"


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
    # Clear contents without removing the directory itself — it's a bind mount
    # so the mount point can't be deleted, only its contents.
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
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=10.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(CALDERA_INTERNAL_URL, headers={"KEY": api_key})
                if resp.status_code < 500:
                    return
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            await asyncio.sleep(5)
    raise TimeoutError(f"Caldera did not become healthy within {timeout}s")


async def _get_ctf_abilities(api_key: str) -> list:
    """Return all CTF abilities (those with 'Recon:' or 'Exploit:' in name)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{CALDERA_INTERNAL_URL}/api/v2/abilities",
            headers={"KEY": api_key},
        )
        resp.raise_for_status()
    return [
        a for a in resp.json()
        if "Recon:" in a.get("name", "") or "Exploit:" in a.get("name", "")
    ]


async def _get_adversaries(api_key: str) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{CALDERA_INTERNAL_URL}/api/v2/adversaries",
            headers={"KEY": api_key},
        )
        resp.raise_for_status()
    return resp.json()


async def _create_operation(api_key: str, adversary_id: str) -> dict:
    payload = {
        "name": "CTF Red Team Emulation",
        "adversary": {"adversary_id": adversary_id},
        "planner": {"id": _ATOMIC_PLANNER_ID},
        "source": {"id": _DEFAULT_SOURCE_ID},
        "group": "red",
        "auto_close": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{CALDERA_INTERNAL_URL}/api/v2/operations",
            headers={"KEY": api_key, "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
        resp.raise_for_status()
    return resp.json()


@router.post("/caldera-setup")
async def caldera_setup(request: Request, db: Session = Depends(get_db)):
    """
    Automated Caldera setup: generates the CTF exploit plugin, installs it,
    restarts Caldera, and creates an adversary operation.

    Replaces the manual Phase 5 steps from the testing runbook.
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
                {"error": f"Caldera container '{CALDERA_CONTAINER_NAME}' not found. Is the stack running?"},
                status_code=400,
            )
        except docker.errors.DockerException as e:
            return JSONResponse({"error": f"Docker error: {e}"}, status_code=500)

        # Step 6: Wait for Caldera to be healthy
        try:
            await _wait_for_caldera(api_key)
        except TimeoutError as e:
            return JSONResponse({"error": str(e)}, status_code=504)

        # Step 7: Verify abilities loaded
        try:
            ctf_abilities = await _get_ctf_abilities(api_key)
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"Failed to list abilities: {e}"}, status_code=502)

        # Step 8: Find "CTF Full Exploit Chain" adversary
        try:
            adversaries = await _get_adversaries(api_key)
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"Failed to list adversaries: {e}"}, status_code=502)

        ctf_adversary = next(
            (a for a in adversaries if a.get("name") == "CTF Full Exploit Chain"), None
        )
        if not ctf_adversary:
            return JSONResponse(
                {
                    "error": "CTF Full Exploit Chain adversary not found after restart",
                    "abilities_loaded": len(ctf_abilities),
                    "adversaries": [a.get("name") for a in adversaries],
                },
                status_code=500,
            )

        # Step 9: Create operation
        operation_result = None
        operation_error = None
        try:
            operation_result = await _create_operation(api_key, ctf_adversary["adversary_id"])
        except httpx.HTTPError as e:
            operation_error = str(e)

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    response = {
        "status": "success" if operation_result else "partial",
        "plugin": {
            "files_copied": file_count,
            "plugin_added_to_config": modified,
            "abilities_loaded": len(ctf_abilities),
            "adversaries_loaded": len([a for a in adversaries if "CTF" in a.get("name", "")]),
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
