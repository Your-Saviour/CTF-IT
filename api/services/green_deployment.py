"""Retry-safe execution of deployment modules on shared green VMs."""

from __future__ import annotations

import json
import shlex

from sqlalchemy.orm import Session

from api.models import GreenDeploymentFact, GreenDeploymentState, Team, utcnow
from api.integrations.expo_it_contract import ExpoData
from api.services.secrets import decrypt_secret, encrypt_secret
from api.services.deployment_facts import resolve_inputs
from api.services.gamenet_provider import ssh_command, upload_text
from builder.ansible import dependency_order
from builder.module_loader import RunStep, load_all_modules


class GreenDeploymentError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _redact(message: str, secrets: list[str]) -> str:
    safe = str(message)
    for value in secrets:
        if value:
            safe = safe.replace(value, "[REDACTED]")
    return safe[:500]


def _authenticated_health(vm, url: str, key_path: str, *, timeout: int = 30) -> tuple[int, str]:
    command = (
        f"curl -kfsS --max-time {timeout} "
        f"-H \"X-API-Key: $(cat {shlex.quote(key_path)})\" "
        f"{shlex.quote(url.rstrip('/') + '/api/v1/data')}"
    )
    code, output, error = ssh_command(vm, command)
    if code:
        return code, error or "Expo-IT management API health check failed"
    try:
        ExpoData.model_validate_json(output)
    except Exception:
        return 1, "Expo-IT management API returned an incompatible contract"
    return 0, ""


def _state(db: Session, vm, module_id: str) -> GreenDeploymentState:
    row = db.query(GreenDeploymentState).filter_by(vm_id=vm.id, module_id=module_id).first()
    if not row:
        row = GreenDeploymentState(vm_id=vm.id, module_id=module_id, status="pending")
        db.add(row); db.flush()
    return row


def execute_green_modules(db: Session, event, vm, module_ids: list[str]) -> dict[str, str]:
    library = {module.id: module for module in load_all_modules()}
    modules = dependency_order([library[module_id] for module_id in module_ids])
    combined: dict[str, str] = {}
    for module in modules:
        if not module.deployment:
            raise GreenDeploymentError("contract_error", f"{module.id} is not deployable")
        state = _state(db, vm, module.id)
        if state.status == "healthy":
            health_url = state.service_url or f"https://{vm.private_ip or vm.ip_address}"
            secret_output = db.query(GreenDeploymentFact).filter_by(
                event_id=event.id, vm_key=vm.green_key, trait="expo_it.api_key",
            ).first()
            health_key_path = f"/tmp/ctf-green-health-{vm.id}-{module.id}"
            if secret_output:
                upload_text(vm, health_key_path, decrypt_secret(secret_output.encrypted_value))
                code, _ = _authenticated_health(vm, health_url, health_key_path, timeout=10)
                ssh_command(vm, f"rm -f {shlex.quote(health_key_path)}")
            else:
                code = 1
            if code == 0:
                combined.update({
                    key: value for key, value in {
                        "expo_it.resolved_commit": state.resolved_commit,
                        "expo_it.private_url": state.service_url,
                    }.items() if value
                })
                if secret_output:
                    combined["expo_it.api_key"] = decrypt_secret(secret_output.encrypted_value)
                continue
            state.status, state.health_status = "pending", "unhealthy"
        state.status, state.current_step = "running", "install"
        state.error_code = state.error_message = None
        db.commit()
        inputs: dict[str, str] = {}
        root = f"/tmp/ctf-green-{vm.id}-{module.id}"
        key_path, output_dir = f"{root}/deploy_key", f"{root}/outputs"
        script_path = f"{root}/install.sh"
        try:
            inputs = resolve_inputs(db, event, vm.green_key, module)
            code, _, error = ssh_command(vm, f"mkdir -p {shlex.quote(output_dir)} && chmod 700 {shlex.quote(root)} {shlex.quote(output_dir)}")
            if code:
                raise GreenDeploymentError("transport_failed", error[:300])
            upload_text(vm, key_path, inputs["git.ssh_private_key"])
            upload_text(vm, script_path, next(
                (module.source_dir / step.script).read_text()
                for step in module.steps if isinstance(step, RunStep)
            ))
            teams = ",".join(row.name for row in db.query(Team).filter_by(event_id=event.id).order_by(Team.id))
            private_url = f"https://{vm.private_ip or vm.ip_address}"
            exports = " ".join([
                f"CTF_DEPLOY_KEY_PATH={shlex.quote(key_path)}",
                f"CTF_DEPLOY_OUTPUT_DIR={shlex.quote(output_dir)}",
                f"EXPO_PRIVATE_URL={shlex.quote(private_url)}",
                f"EXPO_TEAMS={shlex.quote(teams)}",
            ])
            command = f"bash -c {shlex.quote('export ' + exports + '; exec bash ' + script_path)}"
            code, output, error = ssh_command(vm, command, timeout=1800)
            if code:
                raise GreenDeploymentError("build_failed", (error or "Expo-IT build failed")[:300])
            public_outputs = json.loads(output.strip())
            health_url = public_outputs.get("expo_it.private_url", private_url)
            code, error = _authenticated_health(vm, health_url, output_dir + "/expo_it.api_key")
            if code:
                raise GreenDeploymentError("health_check_failed", (error or "Expo-IT health check failed")[:300])
            code, api_key, error = ssh_command(vm, f"cat {shlex.quote(output_dir + '/expo_it.api_key')}")
            if code or not api_key.strip():
                raise GreenDeploymentError("output_failed", (error or "Expo-IT API key missing")[:300])
            combined.update(public_outputs)
            combined["expo_it.api_key"] = api_key.strip()
            secret_output = db.query(GreenDeploymentFact).filter_by(
                event_id=event.id, vm_key=vm.green_key, trait="expo_it.api_key",
            ).first()
            if not secret_output:
                secret_output = GreenDeploymentFact(
                    event_id=event.id, vm_key=vm.green_key, trait="expo_it.api_key",
                    encrypted_value="", secret=True,
                )
                db.add(secret_output)
            secret_output.encrypted_value = encrypt_secret(api_key.strip())
            state.resolved_commit = public_outputs.get("expo_it.resolved_commit")
            state.service_url = public_outputs.get("expo_it.private_url")
            state.health_status = "healthy"
            state.status = "healthy"
            state.completed_at = utcnow()
            db.commit()
        except Exception as exc:
            state.status = "failed"
            state.error_code = getattr(exc, "code", "deployment_failed")
            state.error_message = _redact(str(exc), list(inputs.values()))
            db.commit()
            raise
        finally:
            ssh_command(vm, f"rm -rf {shlex.quote(root)}")
    return combined
