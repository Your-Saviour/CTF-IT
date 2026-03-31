#!/usr/bin/env python3
"""
Build-time state capture.

Runs inside the Docker image during build (after state.json is copied in).
Captures system state that may be needed later for verification — e.g. original
password hashes before the user modifies them.

The snapshot is deliberately broad (all shadow entries, not per-module) so that
the state file reveals nothing about which modules are assigned.
"""
import json

STATE_PATH = "/opt/ctf/state.json"


def snapshot_shadow_hashes() -> dict:
    """Capture all password hashes from /etc/shadow at build time."""
    hashes = {}
    try:
        with open("/etc/shadow") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 2 and parts[0]:
                    hashes[parts[0]] = parts[1]
    except OSError:
        pass
    return hashes


def main():
    with open(STATE_PATH) as f:
        state = json.load(f)

    state["snapshots"]["shadow_hashes"] = snapshot_shadow_hashes()

    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


if __name__ == "__main__":
    main()
