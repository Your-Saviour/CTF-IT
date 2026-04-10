#!/usr/bin/env python3
"""
In-container security audit collector.
Gathers broad system state and prints a JSON payload to stdout
for submission to the CTF platform.

Usage (inside container):
    python3 /opt/ctf/audit.py
"""
import hashlib
import json
import os
import re
import subprocess


def run(cmd):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip(), result.returncode
    except Exception:
        return "", 1


# ---------------------------------------------------------------------------
# Broad scan targets — fixed superset covering all security-relevant paths.
# ---------------------------------------------------------------------------

PERMISSION_PATHS = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/group",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/ssh/sshd_config",
    "/etc/ssh/ssh_host_rsa_key",
    "/etc/ssh/ssh_host_ecdsa_key",
    "/etc/crontab",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/resolv.conf",
    "/etc/fstab",
    "/etc/profile",
    "/etc/bash.bashrc",
    "/etc/login.defs",
    "/etc/securetty",
    "/etc/pam.d/common-auth",
    "/etc/pam.d/common-password",
    "/var/log/auth.log",
    "/var/log/syslog",
    "/root/.ssh/authorized_keys",
    "/root/.bashrc",
    "/root/.profile",
    "/opt/maintenance.sh",
    "/usr/bin/find",
    "/usr/bin/vim",
    "/usr/bin/python3",
    "/usr/bin/perl",
    "/usr/bin/nmap",
    "/usr/bin/wget",
    "/usr/bin/curl",
    "/usr/bin/passwd",
    "/usr/bin/sudo",
    "/usr/bin/su",
    "/usr/bin/chsh",
    "/usr/bin/newgrp",
    "/usr/bin/gpasswd",
    "/usr/bin/chfn",
    "/usr/bin/at",
    "/usr/bin/pkexec",
]

# Directories to glob for permission checks
PERMISSION_DIRS = [
    "/etc/sudoers.d",
    "/etc/cron.d",
    "/etc/cron.daily",
    "/etc/logrotate.d",
]

CONTENT_PATHS = [
    "/etc/ssh/sshd_config",
    "/etc/sudoers",
    "/etc/pam.d/common-auth",
    "/etc/pam.d/common-password",
    "/etc/hosts.allow",
    "/etc/hosts.deny",
    "/etc/login.defs",
    "/etc/securetty",
    "/etc/crontab",
    "/etc/profile",
    "/root/.ssh/authorized_keys",
    "/root/.bashrc",
    "/root/.profile",
    "/opt/inventory/app.py",
    "/opt/inventory/inventory.db.bak",
    "/opt/inventory/uploads/shell.php",
    "/etc/systemd/system/inventory.service",
]

# Directories to glob for content reads
CONTENT_DIRS = [
    "/etc/sudoers.d",
    "/etc/cron.d",
]


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def _list_dir_files(directory):
    """List regular files in a directory, ignoring errors."""
    try:
        return [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f))
        ]
    except OSError:
        return []


def collect_file_permissions():
    paths = list(PERMISSION_PATHS)
    for d in PERMISSION_DIRS:
        paths.extend(_list_dir_files(d))

    result = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        perms, _ = run(f"stat -c '%a' '{path}'")
        owner, _ = run(f"stat -c '%U' '{path}'")
        group, _ = run(f"stat -c '%G' '{path}'")
        result[path] = {"permissions": perms, "owner": owner, "group": group}
    return result


def collect_file_contents():
    paths = list(CONTENT_PATHS)
    for d in CONTENT_DIRS:
        paths.extend(_list_dir_files(d))

    result = {}
    for path in paths:
        try:
            with open(path) as f:
                result[path] = f.read(65536)  # cap at 64 KB per file
        except OSError:
            continue
    return result


def collect_services():
    output, _ = run(
        "systemctl list-units --type=service --all --no-pager --plain"
    )
    services = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].endswith(".service"):
            name = parts[0].removesuffix(".service")
            status, _ = run(f"systemctl is-active {name}")
            services[name] = status
    return services


def collect_packages():
    output, _ = run("dpkg-query -W -f='${Package}\\n'")
    return [p for p in output.splitlines() if p]


def collect_listening_ports():
    output, _ = run("ss -tlnp")
    ports = set()
    for line in output.splitlines()[1:]:  # skip header
        match = re.search(r":(\d+)\s", line)
        if match:
            ports.add(int(match.group(1)))
    return sorted(ports)


def collect_http_responses(listening_ports):
    """Probe all listening ports for HTTP responses.

    Labels are formatted as "localhost_{port}" — this is the contract
    that verification YAMLs reference via the "label" field.
    """
    result = {}
    for port in listening_ports:
        label = f"localhost_{port}"
        url = f"http://localhost:{port}/"
        # Single request: body followed by status code on the last line
        raw, rc = run(
            f"curl -s -w '\\n%{{http_code}}' --max-time 3 --max-filesize 65536 '{url}'"
        )
        if rc != 0:
            continue
        lines = raw.rsplit('\n', 1)
        body = lines[0] if len(lines) == 2 else ""
        status = lines[1] if len(lines) == 2 else lines[0]
        result[label] = {
            "status_code": int(status) if status.isdigit() else 0,
            "body": body[:65536],
        }
    return result


def collect_processes():
    """Collect running process command strings."""
    output, _ = run("ps aux --no-headers")
    processes = []
    for line in output.splitlines():
        parts = line.split(None, 10)
        if len(parts) >= 11:
            processes.append(parts[10])
    return processes


def collect_shadow_hashes():
    hashes = {}
    try:
        with open("/etc/shadow") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 2:
                    hashes[parts[0]] = parts[1]
    except OSError:
        pass
    return hashes


def collect_passwd_users():
    """Collect usernames from /etc/passwd for user_not_exists verification."""
    users = []
    try:
        with open("/etc/passwd") as f:
            for line in f:
                parts = line.strip().split(":")
                if parts and parts[0]:
                    users.append(parts[0])
    except OSError:
        pass
    return users


def collect_cron_entries():
    """Collect all active (non-comment) cron entries for cron_not_present verification."""
    entries = []

    def _read_cron_file(path):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        entries.append(line)
        except OSError:
            pass

    _read_cron_file("/etc/crontab")
    for cron_dir in ["/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly",
                     "/etc/cron.weekly", "/etc/cron.monthly"]:
        for fpath in _list_dir_files(cron_dir):
            _read_cron_file(fpath)

    for cmd in ["crontab -l 2>/dev/null", "crontab -u root -l 2>/dev/null"]:
        output, _ = run(cmd)
        for line in output.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line)

    return entries


def collect_file_existence(state: dict) -> dict:
    """Check existence of files listed in state.json check_paths.

    Used by file_absent verification to confirm payload files were removed.
    """
    paths = state.get("check_paths", [])
    return {path: os.path.exists(path) for path in paths}


def collect_file_hashes(state: dict) -> dict:
    """Hash files listed in state.json hash_paths.

    Used by file_hash_changed verification to detect payload modifications.
    """
    paths = state.get("hash_paths", [])
    hashes = {}
    for path in paths:
        try:
            with open(path, "rb") as f:
                hashes[path] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            hashes[path] = None
    return hashes


def read_flag():
    try:
        with open("/root/flag.txt") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_state():
    try:
        with open("/opt/ctf/state.json") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"user_id": "", "snapshots": {}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    state = read_state()
    ports = collect_listening_ports()
    snapshot = {
        "user_id": state.get("user_id", ""),
        "flag": read_flag(),
        "build_state": state.get("snapshots", {}),
        "file_permissions": collect_file_permissions(),
        "file_contents": collect_file_contents(),
        "services": collect_services(),
        "packages": collect_packages(),
        "listening_ports": ports,
        "http_responses": collect_http_responses(ports),
        "processes": collect_processes(),
        "shadow_hashes": collect_shadow_hashes(),
        "passwd_users": collect_passwd_users(),
        "cron_entries": collect_cron_entries(),
        "file_existence": collect_file_existence(state),
        "file_hashes": collect_file_hashes(state),
    }
    print(json.dumps(snapshot))


if __name__ == "__main__":
    main()
