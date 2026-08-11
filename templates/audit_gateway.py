#!/usr/bin/env python3
"""Root-owned forced-command gateway for structured, read-only CTF checks."""
import json
import os
import re
import subprocess
import sys

IDENT = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
PATH = re.compile(r"^/(?!.*(?:^|/)\.\.(?:/|$))[^\x00\r\n]{0,1023}$")


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, shell=False)
    return result.returncode, result.stdout.strip()[:4096]


def valid(value, pattern):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def check(spec):
    kind = {"password_changed":"password_hash_changed", "port_closed":"listening_port",
            "process_running":"process_state", "user_not_exists":"user_absent"}.get(spec.get("type"), spec.get("type"))
    if kind in {"all_of", "any_of"}:
        values = [check(child)[0] == 0 for child in spec.get("checks", [])]
        passed = all(values) if kind == "all_of" else any(values)
        return (0 if passed else 1), ""
    if kind in {"file_contains", "file_not_contains"}:
        if not valid(spec.get("path"), PATH) or not isinstance(spec.get("pattern"), str): raise ValueError()
        status, value = run(["/usr/bin/grep", "-Fq", "--", spec["pattern"], spec["path"]])
        return ((0 if status else 1), "") if kind == "file_not_contains" else (status, value)
    if kind in {"file_exists", "file_absent"}:
        if not valid(spec.get("path"), PATH): raise ValueError()
        exists = os.path.exists(spec["path"])
        return (0 if (exists == (kind == "file_exists")) else 1), ""
    if kind == "file_permissions":
        if not valid(spec.get("path"), PATH): raise ValueError()
        mode = str(spec.get("mode", spec.get("expected", ""))).lstrip("0")
        actual = oct(os.stat(spec["path"]).st_mode & 0o7777)[2:]
        return (0 if actual == mode else 1), actual
    if kind in {"service_running", "service_state"}:
        if not valid(spec.get("service"), IDENT): raise ValueError()
        status, value = run(["/usr/bin/systemctl", "is-active", "--", spec["service"]])
        expected = spec.get("expected", "active")
        return (status if expected == "active" else (0 if status else 1)), value
    if kind == "process_state":
        process = spec.get("process")
        if not isinstance(process, str) or len(process) > 256: raise ValueError()
        if re.fullmatch(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):\d{1,5}", process):
            status, output = run(["/usr/bin/ss", "-H", "-lntup"]); found = process in output
        else:
            status, _ = run(["/usr/bin/pgrep", "-f", "--", process]); found = status == 0
        desired = spec.get("expected", "running") == "running"
        return (0 if found == desired else 1), ""
    if kind == "listening_port":
        port = spec.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535: raise ValueError()
        _, output = run(["/usr/bin/ss", "-H", "-lntup"])
        found = bool(re.search(rf":{port}\b", output))
        desired = False if spec.get("type") == "port_closed" else spec.get("listening", True)
        return (0 if found == desired else 1), ""
    if kind == "package_installed":
        if not valid(spec.get("package"), IDENT): raise ValueError()
        status, output = run(["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}", "--", spec["package"]])
        return (0 if status == 0 and output == "installed" else 1), ""
    if kind == "jar_library_version":
        path, library, minimum = spec.get("path"), spec.get("library"), spec.get("minimum")
        if not valid(path, PATH) or not valid(library, IDENT) or not re.fullmatch(r"\d+\.\d+\.\d+", str(minimum)): raise ValueError()
        status, output = run(["/usr/bin/unzip", "-Z1", path])
        versions = re.findall(rf"(?:^|/){re.escape(library)}-(\d+\.\d+\.\d+)\.jar$", output, re.M)
        def version(value): return tuple(int(part) for part in value.split("."))
        passed = status == 0 and bool(versions) and min(version(item) for item in versions) >= version(minimum)
        return (0 if passed else 1), min(versions) if versions else ""
    if kind == "json_version_at_least":
        path, key, minimum = spec.get("path"), spec.get("key"), spec.get("minimum")
        if not valid(path, PATH) or not valid(key, IDENT) or not re.fullmatch(r"\d+\.\d+\.\d+", str(minimum)): raise ValueError()
        with open(path, encoding="utf-8") as handle: data = json.load(handle)
        value = data
        for part in key.split("."): value = value[part]
        def version(item): return tuple(int(part) for part in str(item).lstrip("^~>= ").split(".")[:3])
        return (0 if version(value) >= version(minimum) else 1), str(value)
    if kind == "docker_container_not_privileged":
        container = spec.get("container")
        if not valid(container, IDENT): raise ValueError()
        status, output = run(["/usr/bin/docker", "inspect", "--format", "{{.HostConfig.Privileged}}", container])
        return (0 if status != 0 or output == "false" else 1), output
    if kind == "ufw_default_deny":
        status, output = run(["/usr/sbin/ufw", "status", "verbose"])
        passed = status == 0 and "Status: active" in output and re.search(r"Default:\s+deny \(incoming\)", output)
        return (0 if passed else 1), ""
    if kind == "sysctl_value":
        key, expected = spec.get("key"), str(spec.get("expected"))
        if not valid(key, IDENT): raise ValueError()
        status, output = run(["/usr/sbin/sysctl", "-n", key])
        return (0 if status == 0 and output == expected else 1), output
    if kind == "sshd_effective_option":
        option, expected = spec.get("option"), spec.get("expected")
        if not valid(option, IDENT) or not valid(expected, IDENT): raise ValueError()
        status, output = run(["/usr/sbin/sshd", "-T"])
        values = dict(line.split(None, 1) for line in output.splitlines() if " " in line)
        return (0 if status == 0 and values.get(option.lower()) == expected.lower() else 1), values.get(option.lower(), "")
    if kind == "cron_not_present":
        user, pattern = spec.get("user", "root"), spec.get("pattern")
        if not valid(user, IDENT) or not isinstance(pattern, str): raise ValueError()
        _, output = run(["/usr/bin/crontab", "-u", user, "-l"])
        return (0 if pattern not in output else 1), ""
    if kind == "user_absent":
        user = spec.get("username", spec.get("user"))
        if not valid(user, IDENT): raise ValueError()
        status, _ = run(["/usr/bin/getent", "passwd", user])
        return (0 if status else 1), ""
    if kind in {"file_hash_changed", "password_hash_changed"}:
        if kind == "file_hash_changed":
            if not valid(spec.get("path"), PATH): raise ValueError()
            return run(["/usr/bin/sha256sum", "--", spec["path"]])
        user = spec.get("username", spec.get("user"))
        if not valid(user, IDENT): raise ValueError()
        status, output = run(["/usr/bin/getent", "shadow", user])
        return status, output.split(":", 2)[1] if status == 0 and ":" in output else ""
    raise ValueError()


try:
    payload = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if len(payload) > 8192: raise ValueError()
    status, value = check(json.loads(payload))
    print(json.dumps({"status": status, "value": value}))
except Exception:
    print(json.dumps({"status": 2, "value": ""}))
    sys.exit(2)
