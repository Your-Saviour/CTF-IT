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
from api.models import Event, VM, VMModule
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient

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
    """Add required plugins to the plugins list if not already present. Returns True if modified."""
    plugins = config.setdefault("plugins", [])
    modified = False
    # stockpile provides the plain-text obfuscator required by the atomic planner
    for plugin in ("stockpile", "ctf-exploit"):
        if plugin not in plugins:
            plugins.append(plugin)
            modified = True
    return modified


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


async def _wait_for_event_agent(caldera: CalderaClient, event_id: int, db: Session, timeout: int = 90) -> bool:
    """Wait for an agent belonging to an active VM in this event to reconnect after Caldera restarts."""
    active_vms = db.query(VM).filter(
        VM.event_id == event_id,
        VM.status == "active"
    ).all()
    if not active_vms:
        return False
    vm_ips = {vm.ip_address for vm in active_vms if vm.ip_address}
    if not vm_ips:
        return False
    group = f"event-{event_id}"
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            agents = await caldera.list_agents()
            for agent in agents:
                if agent.get("group") != group:
                    continue
                agent_ips = agent.get("host_ip_addrs", []) or []
                if any(ip in vm_ips for ip in agent_ips):
                    return True
        except Exception:
            pass
        await asyncio.sleep(5)
    return False


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

    _event_id = body.get("event_id")  # used for operation group targeting
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
                op_group = f"event-{_event_id}" if _event_id else "red"
                if _event_id and not await _wait_for_event_agent(caldera, _event_id, db):
                    operation_error = f"No Caldera agent reconnected to {op_group} after restart"
                else:
                    operation_result = await caldera.create_operation(
                        name="CTF Red Team Emulation",
                        adversary_id=ctf_adversary["adversary_id"],
                        planner_id=planner_id,
                        group=op_group,
                    )
            except Exception as e:
                operation_error = str(e)

        # Store attack trees on VMs for this event
        if _event_id:
            from builder.attack_tree import build_attack_tree, serialize_tree
            from builder.module_loader import load_all_modules
            all_library = {m.id: m for m in load_all_modules()}
            event_vms = db.query(VM).filter(VM.event_id == _event_id).all()
            for ev_vm in event_vms:
                vm_mods = db.query(VMModule).filter(VMModule.vm_id == ev_vm.id).all()
                vm_module_objects = [all_library.get(vmm.module_id) for vmm in vm_mods]
                vm_module_objects = [m for m in vm_module_objects if m]
                if vm_module_objects:
                    tree = build_attack_tree(vm_module_objects)
                    import json as _json
                    ev_vm.attack_tree_json = _json.dumps(serialize_tree(tree))
            db.commit()

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
