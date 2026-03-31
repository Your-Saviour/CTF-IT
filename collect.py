#!/usr/bin/env python3
"""
In-container verification collector.
Reads /opt/ctf/manifest.json, collects system state for each module,
and prints a JSON payload to stdout for submission to the platform.
"""
import json
import os
import subprocess


def run(cmd):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip(), result.returncode
    except Exception:
        return "", 1


def collect_file_permissions(verification):
    path = verification["path"]
    perms, _ = run(f"stat -c '%a' {path}")
    owner, _ = run(f"stat -c '%U' {path}")
    group, _ = run(f"stat -c '%G' {path}")
    return {"permissions": perms, "owner": owner, "group": group}


def collect_file_contains(verification):
    path = verification["path"]
    try:
        with open(path) as f:
            content = f.read()
    except Exception:
        content = ""
    return {"content": content}


def collect_service_running(verification):
    service = verification["service"]
    status, _ = run(f"systemctl is-active {service}")
    return {"status": status}


def collect_package_installed(verification):
    package = verification["package"]
    _, code = run(f"dpkg -l {package} 2>/dev/null | grep -q '^ii'")
    return {"installed": code == 0}


def collect_port_closed(verification):
    port = verification["port"]
    output, _ = run(f"ss -tlnp")
    listening = f":{port} " in output or f":{port}\t" in output
    return {"listening": listening}


def collect_flag_contents(verification):
    path = verification["path"]
    try:
        with open(path) as f:
            contents = f.read().strip()
    except Exception:
        contents = ""
    return {"contents": contents}


def collect_password_not_default(verification):
    user = verification["user"]
    try:
        with open("/etc/shadow") as f:
            for line in f:
                parts = line.strip().split(":")
                if parts[0] == user:
                    hash_val = parts[1]
                    is_default = hash_val in ("", "!", "*", "!!", "!*")
                    return {"is_default": is_default}
    except Exception:
        pass
    return {"is_default": True}


def collect_password_changed(verification):
    user = verification["user"]
    original_hash = verification.get("original_hash", "")
    current_hash = ""
    try:
        with open("/etc/shadow") as f:
            for line in f:
                parts = line.strip().split(":")
                if parts[0] == user:
                    current_hash = parts[1]
                    break
    except Exception:
        pass
    return {"current_hash": current_hash, "original_hash": original_hash}


COLLECTORS = {
    "file_permissions": collect_file_permissions,
    "file_contains": collect_file_contains,
    "file_not_contains": collect_file_contains,
    "service_running": collect_service_running,
    "package_installed": collect_package_installed,
    "port_closed": collect_port_closed,
    "flag_contents": collect_flag_contents,
    "password_not_default": collect_password_not_default,
    "password_changed": collect_password_changed,
}


def main():
    manifest_path = "/opt/ctf/manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    results = []
    for module in manifest["modules"]:
        vtype = module["verification"]["type"]
        collector = COLLECTORS.get(vtype)
        if collector:
            collected = collector(module["verification"])
        else:
            collected = {}

        results.append({
            "module_id": module["id"],
            "collected": collected,
        })

    payload = {
        "user_id": manifest["user_id"],
        "results": results,
    }
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
