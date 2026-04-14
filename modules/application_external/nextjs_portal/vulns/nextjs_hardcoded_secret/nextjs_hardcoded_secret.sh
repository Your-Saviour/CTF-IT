#!/bin/bash
set -e
# The hardcoded NEXTAUTH_SECRET=super-secret-dev-key is already present in
# the portal.service unit file deployed by the parent module.
# This script is a no-op.
grep -q 'super-secret-dev-key' /etc/systemd/system/portal.service || true
