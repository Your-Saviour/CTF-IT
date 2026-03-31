#!/usr/bin/env python3
"""
In-container security audit collector.
Gathers broad system state and prints a JSON payload to stdout
for submission to the CTF platform.

Usage (inside container):
    python3 /opt/ctf/audit.py
"""
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
    snapshot = {
        "user_id": state.get("user_id", ""),
        "flag": read_flag(),
        "build_state": state.get("snapshots", {}),
        "file_permissions": collect_file_permissions(),
        "file_contents": collect_file_contents(),
        "services": collect_services(),
        "packages": collect_packages(),
        "listening_ports": collect_listening_ports(),
        "shadow_hashes": collect_shadow_hashes(),
    }
    print(json.dumps(snapshot))


if __name__ == "__main__":
    main()
