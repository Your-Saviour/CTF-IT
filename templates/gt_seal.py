#!/usr/bin/env python3
"""One-shot provisioning helper that records the expected gt checker state."""

import grp
import hashlib
import json
import os
import pwd
import stat

GT_USER = "gt"
SEAL_PATH = "/etc/ctf/gt-integrity.json"
GATEWAY_PATH = "/usr/local/sbin/ctf-audit-gateway"
SUDOERS_PATH = "/etc/sudoers.d/gt"
AUTHORIZED_KEYS_PATH = "/home/gt/.ssh/authorized_keys"


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def metadata(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("checker paths cannot be symlinks")
    return {"uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}


def account_state(account):
    with open("/etc/shadow", encoding="utf-8") as handle:
        shadow = next(line.rstrip("\n") for line in handle if line.startswith(GT_USER + ":"))
    groups = sorted(group.gr_name for group in grp.getgrall() if GT_USER in group.gr_mem)
    primary = grp.getgrgid(account.pw_gid)
    return {"uid": account.pw_uid, "gid": account.pw_gid, "home": account.pw_dir,
            "shell": account.pw_shell, "gecos": account.pw_gecos,
            "shadow_sha256": hashlib.sha256(shadow.encode()).hexdigest(), "groups": groups,
            "primary_group": {"name": primary.gr_name, "gid": primary.gr_gid,
                              "members": sorted(primary.gr_mem)}}


def main():
    if os.geteuid() != 0:
        raise PermissionError("sealing requires root")
    expected_key = os.environ.get("GT_AUTHORIZED_KEY")
    if not expected_key:
        raise ValueError("missing expected key")
    with open(AUTHORIZED_KEYS_PATH, encoding="utf-8") as handle:
        if handle.read().rstrip("\n") != expected_key:
            raise ValueError("authorized key does not match")
    account = pwd.getpwnam(GT_USER)
    seal = {
        "account": account_state(account),
        "files": {
            GATEWAY_PATH: {**metadata(GATEWAY_PATH), "sha256": digest(GATEWAY_PATH)},
            SUDOERS_PATH: {**metadata(SUDOERS_PATH), "sha256": digest(SUDOERS_PATH)},
            AUTHORIZED_KEYS_PATH: {**metadata(AUTHORIZED_KEYS_PATH), "sha256": digest(AUTHORIZED_KEYS_PATH)},
            "/home/gt": metadata("/home/gt"),
            "/home/gt/.ssh": metadata("/home/gt/.ssh"),
        },
    }
    temporary = SEAL_PATH + ".new"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(seal, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o400)
    os.replace(temporary, SEAL_PATH)


if __name__ == "__main__":
    main()
