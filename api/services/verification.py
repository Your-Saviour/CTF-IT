"""Structured, allow-listed verification against assigned VMs.

Module data is validated and translated into fixed audit commands here. No
module-provided shell text is ever executed.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shlex
import time
from dataclasses import dataclass
import httpx
from sqlalchemy.orm import Session

from api.models import User, VerificationAttempt, VM, VMModule, utcnow

CHECK_TIMEOUT = float(os.environ.get("VERIFICATION_TIMEOUT_SECONDS", "10"))
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
_PROCESS = re.compile(r"^[A-Za-z0-9_./@:+-]{1,256}$")
_SAFE_PATTERN = re.compile(r"^[^\x00\r\n]{1,512}$")
_PATH = re.compile(r"^/(?!.*(?:^|/)\.\.(?:/|$))[^\x00\r\n]{0,1023}$")
_vm_locks: dict[int, asyncio.Lock] = {}


def vm_is_busy(vm_id: int) -> bool:
    lock = _vm_locks.get(vm_id)
    return bool(lock and lock.locked())


class InvalidSpecification(ValueError):
    pass


@dataclass(frozen=True)
class VerificationResult:
    result: str  # pass/fail/unavailable/invalid
    summary: str
    error_code: str | None = None
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.result == "pass"


def _string(spec: dict, key: str, pattern: re.Pattern = _SAFE_PATTERN) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise InvalidSpecification(f"invalid {key}")
    return value


def validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise InvalidSpecification("verification must be an object")
    kind = spec.get("type")
    aliases = {"password_changed": "password_hash_changed", "port_closed": "listening_port",
               "process_running": "process_state", "user_not_exists": "user_absent"}
    kind = aliases.get(kind, kind)
    if kind in {"all_of", "any_of"}:
        checks = spec.get("checks")
        if not isinstance(checks, list) or not 1 <= len(checks) <= 20:
            raise InvalidSpecification(f"{kind} requires 1-20 checks")
        for check in checks:
            validate_spec(check)
        return
    if kind in {"file_contains", "file_not_contains"}:
        _string(spec, "path", _PATH)
        _string(spec, "pattern")
    elif kind in {"file_exists", "file_absent", "file_hash_changed", "file_permissions"}:
        _string(spec, "path", _PATH)
        if kind == "file_permissions" and not re.fullmatch(r"[0-7]{3,4}", str(spec.get("mode", spec.get("expected", "")))):
            raise InvalidSpecification("invalid mode")
    elif kind in {"service_running", "service_state"}:
        _string(spec, "service", _IDENTIFIER)
        if spec.get("expected", "active") not in {"active", "inactive", "failed"}:
            raise InvalidSpecification("invalid service state")
    elif kind == "process_state":
        _string(spec, "process", _PROCESS)
        if spec.get("expected", "running") not in {"running", "stopped"}:
            raise InvalidSpecification("running must be boolean")
    elif kind == "listening_port":
        port = spec.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise InvalidSpecification("invalid port")
        if not isinstance(spec.get("listening", True), bool):
            raise InvalidSpecification("listening must be boolean")
    elif kind == "package_installed":
        _string(spec, "package", _IDENTIFIER)
    elif kind == "jar_library_version":
        _string(spec, "path", _PATH)
        _string(spec, "library", _IDENTIFIER)
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(spec.get("minimum", ""))):
            raise InvalidSpecification("invalid minimum version")
    elif kind == "json_version_at_least":
        _string(spec, "path", _PATH)
        _string(spec, "key", _IDENTIFIER)
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(spec.get("minimum", ""))):
            raise InvalidSpecification("invalid minimum version")
    elif kind == "docker_container_not_privileged":
        _string(spec, "container", _IDENTIFIER)
    elif kind == "ufw_default_deny":
        pass
    elif kind == "sysctl_value":
        _string(spec, "key", _IDENTIFIER)
        if not isinstance(spec.get("expected"), (str, int)) or len(str(spec["expected"])) > 64:
            raise InvalidSpecification("invalid sysctl value")
    elif kind == "sshd_effective_option":
        _string(spec, "option", _IDENTIFIER)
        _string(spec, "expected", _IDENTIFIER)
    elif kind == "cron_not_present":
        _string(spec, "pattern")
        if "user" in spec:
            _string(spec, "user", _IDENTIFIER)
    elif kind in {"user_absent", "password_hash_changed"}:
        value = spec.get("username", spec.get("user"))
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise InvalidSpecification("invalid username")
    elif kind == "http_response":
        port = spec.get("port", 80)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise InvalidSpecification("invalid HTTP port")
        path = spec.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/") or ".." in path or len(path) > 1024:
            raise InvalidSpecification("invalid HTTP path")
        for field in ("body_contains", "body_not_contains"):
            if field in spec:
                _string(spec, field)
        if "status_code" in spec and not isinstance(spec["status_code"], int):
            raise InvalidSpecification("invalid HTTP status")
        if "method" in spec and spec["method"] not in {"GET", "POST"}:
            raise InvalidSpecification("invalid HTTP method")
        if "form" in spec and not isinstance(spec["form"], dict):
            raise InvalidSpecification("invalid HTTP form")
        if "form" in spec:
            if len(spec["form"]) > 20 or any(
                not isinstance(key, str) or not _IDENTIFIER.fullmatch(key)
                or not isinstance(value, str) or not _SAFE_PATTERN.fullmatch(value)
                for key, value in spec["form"].items()
            ):
                raise InvalidSpecification("invalid HTTP form")
    else:
        raise InvalidSpecification(f"unsupported verification type: {kind}")


def baseline_requirements(spec: dict) -> list[tuple[str, str]]:
    kind = {"password_changed": "password_hash_changed"}.get(spec.get("type"), spec.get("type"))
    if kind in {"all_of", "any_of"}:
        return [item for child in spec.get("checks", []) for item in baseline_requirements(child)]
    if kind == "file_hash_changed":
        return [("file", spec["path"])]
    if kind == "password_hash_changed":
        return [("password", spec.get("username", spec.get("user")))]
    return []


def _command(spec: dict) -> str:
    kind = {"password_changed": "password_hash_changed", "port_closed": "listening_port",
            "process_running": "process_state", "user_not_exists": "user_absent"}.get(spec["type"], spec["type"])
    if kind in {"file_contains", "file_not_contains"}:
        return f"grep -Fq -- {shlex.quote(spec['pattern'])} {shlex.quote(spec['path'])}"
    if kind in {"file_exists", "file_absent"}:
        return f"test -e {shlex.quote(spec['path'])}"
    if kind == "file_permissions":
        mode = str(spec.get("mode", spec.get("expected"))).lstrip("0")
        return f"test \"$(stat -c %a -- {shlex.quote(spec['path'])})\" = {shlex.quote(mode)}"
    if kind in {"service_running", "service_state"}:
        return f"systemctl is-active --quiet -- {shlex.quote(spec['service'])}"
    if kind == "process_state":
        process = spec["process"]
        if re.fullmatch(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):\d{1,5}", process):
            return f"ss -H -lntup | grep -Fq -- {shlex.quote(process)}"
        return f"pgrep -f -- {shlex.quote(process)} >/dev/null"
    if kind == "listening_port":
        return f"ss -H -lntup 'sport = :{spec['port']}' | grep -q ."
    if kind == "package_installed":
        return f"dpkg-query -W -f='${{db:Status-Status}}' -- {shlex.quote(spec['package'])} | grep -qx installed"
    if kind == "cron_not_present":
        user = spec.get("user", "root")
        return f"! crontab -u {shlex.quote(user)} -l 2>/dev/null | grep -Fq -- {shlex.quote(spec['pattern'])}"
    if kind == "user_absent":
        return f"! getent passwd -- {shlex.quote(spec.get('username', spec.get('user')))} >/dev/null"
    if kind == "file_hash_changed":
        return f"sha256sum -- {shlex.quote(spec['path'])} | cut -d' ' -f1"
    if kind == "password_hash_changed":
        return f"getent shadow -- {shlex.quote(spec.get('username', spec.get('user')))} | cut -d: -f2"
    raise InvalidSpecification(f"no SSH command for {kind}")


async def _ssh(vm: VM, spec: dict) -> tuple[int, str]:
    def execute() -> tuple[int, str]:
        import paramiko
        from api.database import SessionLocal
        from api.services.verifier_account import connect_verifier, mark_verifier_tampered
        db = SessionLocal()
        client = None
        try:
            current = db.query(VM).filter(VM.id == vm.id).first()
            if not current:
                return 255, ""
            client = connect_verifier(current, db)
            _, stdout, _ = client.exec_command(json.dumps(spec, separators=(",", ":")), timeout=CHECK_TIMEOUT)
            output = stdout.read(4096).decode("utf-8", "replace").strip()
            transport_status = stdout.channel.recv_exit_status()
            if transport_status not in {0, 1, 2, 3}:
                return 255, ""
            payload = json.loads(output)
            status = int(payload["status"])
            if status == 3:
                mark_verifier_tampered(db, vm.id)
            return status, str(payload.get("value", ""))
        except paramiko.AuthenticationException:
            mark_verifier_tampered(db, vm.id)
            return 3, ""
        except Exception:
            return 255, ""
        finally:
            if client:
                client.close()
            db.close()
    return await asyncio.wait_for(asyncio.to_thread(execute), timeout=CHECK_TIMEOUT + 2)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def capture_baseline(vm: VM, spec: dict) -> dict[str, str]:
    validate_spec(spec)
    baseline = {}
    for kind, identifier in baseline_requirements(spec):
        child = {"type": "file_hash_changed", "path": identifier} if kind == "file" else {
            "type": "password_hash_changed", "username": identifier,
        }
        status, output = await _ssh(vm, child)
        if status != 0 or not output:
            raise RuntimeError("baseline unavailable")
        baseline[f"{kind}:{identifier}"] = _fingerprint(output)
    return baseline


async def _evaluate(spec: dict, vm: VM, baseline: dict[str, str], ssh_executor=None) -> tuple[bool, bool]:
    kind = {"password_changed": "password_hash_changed", "port_closed": "listening_port",
            "process_running": "process_state", "user_not_exists": "user_absent"}.get(spec["type"], spec["type"])
    if kind in {"all_of", "any_of"}:
        values = [await _evaluate(child, vm, baseline, ssh_executor) for child in spec["checks"]]
        if any(unavailable for _, unavailable in values):
            return False, True
        passed = [value for value, _ in values]
        return (all(passed) if kind == "all_of" else any(passed)), False
    if kind == "http_response":
        try:
            ipaddress.ip_address(vm.ip_address)
        except ValueError:
            return False, True
        url = f"http://{vm.ip_address}:{spec.get('port', 80)}{spec.get('path', '/')}"
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, follow_redirects=False) as client:
                if spec.get("method", "GET") == "POST":
                    response = await client.post(url, data=spec.get("form"))
                else:
                    response = await client.get(url)
        except (httpx.HTTPError, asyncio.TimeoutError):
            return False, True
        passed = True
        if "status_code" in spec:
            passed &= response.status_code == spec["status_code"]
        if "body_contains" in spec:
            passed &= spec["body_contains"] in response.text
        if "body_not_contains" in spec:
            passed &= spec["body_not_contains"] not in response.text
        return passed, False

    status, output = await (ssh_executor(spec) if ssh_executor else _ssh(vm, spec))
    if status == 255:
        return False, True
    if kind in {"file_not_contains", "file_absent"}:
        return status == 0, False
    if kind in {"service_running", "service_state", "process_state", "listening_port"}:
        return status == 0, False
    if kind in {"file_hash_changed", "password_hash_changed"}:
        identifier = spec.get("path") or spec.get("username") or spec.get("user")
        prefix = "file" if kind == "file_hash_changed" else "password"
        expected = baseline.get(f"{prefix}:{identifier}")
        if not expected or status != 0 or not output:
            return False, True
        return _fingerprint(output) != expected, False
    return status == 0, False


async def verify_spec(spec: dict, vm: VM, baseline: dict[str, str] | None = None, ssh_executor=None) -> VerificationResult:
    started = time.monotonic()
    try:
        validate_spec(spec)
        # Every scoring path, including API-originated HTTP checks, first proves
        # that the VM's protected gt checker boundary is intact.
        integrity_status, _ = await (
            ssh_executor({"type": "integrity"}) if ssh_executor else _ssh(vm, {"type": "integrity"})
        )
        if integrity_status != 0:
            code = "checker_integrity_failed" if integrity_status == 3 else "target_unavailable"
            return VerificationResult("unavailable", "Scoring is disabled because the VM checker is unavailable.",
                                      code, int((time.monotonic()-started)*1000))
        passed, unavailable = await _evaluate(spec, vm, baseline or {}, ssh_executor)
        if unavailable:
            return VerificationResult("unavailable", "Verification is temporarily unavailable.", "target_unavailable", int((time.monotonic()-started)*1000))
        return VerificationResult("pass" if passed else "fail", "Remediation verified." if passed else "The remediation is not complete yet.", None, int((time.monotonic()-started)*1000))
    except InvalidSpecification:
        return VerificationResult("invalid", "This exercise has an invalid verification definition.", "invalid_specification", int((time.monotonic()-started)*1000))
    except (asyncio.TimeoutError, TimeoutError):
        return VerificationResult("unavailable", "Verification is temporarily unavailable.", "verification_timeout", int((time.monotonic()-started)*1000))


async def verify_assignment(db: Session, assignment: VMModule, spec: dict, trigger: str, user: User | None = None, ssh_executor=None) -> VerificationResult:
    if trigger not in {"learner", "periodic", "admin"}:
        raise ValueError("invalid verification trigger")
    lock = _vm_locks.setdefault(assignment.vm_id, asyncio.Lock())
    async with lock:
        baseline = json.loads(assignment.verification_baseline_json or "{}")
        result = await verify_spec(spec, assignment.vm, baseline, ssh_executor)
        db.expire_all()
        assignment = db.query(VMModule).filter(VMModule.id == assignment.id).with_for_update().populate_existing().one()
        now = utcnow()
        assignment.last_verified_at = now
        assignment.verification_error_code = result.error_code
        if result.passed:
            if not assignment.first_completed_at:
                assignment.first_completed_at = now
            assignment.status = "completed"
            assignment.completed = True
            assignment.completed_at = now
            assignment.completed_by_id = user.id if user else None
        elif result.result == "fail" and trigger == "periodic" and assignment.status == "completed":
            assignment.status = "regressed"
            assignment.completed = False
            assignment.completed_at = None
            assignment.completed_by_id = None
        db.add(VerificationAttempt(
            module_assignment_id=assignment.id,
            user_id=user.id if user else None,
            trigger_type=trigger,
            result=result.result,
            safe_summary=result.summary,
            error_code=result.error_code,
            duration_ms=result.duration_ms,
        ))
        db.commit()
        db.refresh(assignment)
        return result
