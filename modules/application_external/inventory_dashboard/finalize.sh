#!/bin/bash
set -e

python3 /tmp/init_db.py && rm /tmp/init_db.py

# Enable the service via symlink (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/inventory.service /etc/systemd/system/multi-user.target.wants/inventory.service

# Start the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start inventory.service
fi
