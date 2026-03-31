#!/usr/bin/env python3
"""
Build-time manifest enrichment.

Runs inside the Docker image during build (after manifest.json is copied in).
Scans modules for verification types that need build-time system state and
injects that state into the manifest so collect.py can reference it later.

To add a new snapshot:
  1. Write a function that takes a verification dict and returns a dict of
     key/value pairs to merge into it.
  2. Register it in SNAPSHOTS with the verification type as the key.
"""
import json

MANIFEST_PATH = "/opt/ctf/manifest.json"


# ---------- snapshot functions ----------
# Each takes the module's verification dict and returns a dict of fields
# to merge into it. These run as root during docker build.


def snapshot_password_hash(verification: dict) -> dict:
    """Capture the original password hash for a user from /etc/shadow."""
    user = verification.get("user", "")
    with open("/etc/shadow") as f:
        for line in f:
            parts = line.strip().split(":")
            if len(parts) >= 2 and parts[0] == user:
                return {"original_hash": parts[1]}
    return {"original_hash": ""}


# ---------- registry ----------
# Map verification type -> snapshot function.
# Only types listed here will be processed.

SNAPSHOTS = {
    "password_changed": snapshot_password_hash,
}


# ---------- main ----------

def main():
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    updated = False
    for m in manifest["modules"]:
        vtype = m["verification"].get("type")
        snapshot_fn = SNAPSHOTS.get(vtype)
        if snapshot_fn:
            extra = snapshot_fn(m["verification"])
            m["verification"].update(extra)
            updated = True

    if updated:
        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2)


if __name__ == "__main__":
    main()
