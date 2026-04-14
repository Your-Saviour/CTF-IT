#!/bin/bash
set -e

# Create monitord system user
useradd -r -s /bin/false monitord 2>/dev/null || true

# Set ownership and permissions
chown -R monitord:monitord /opt/monitord/ /var/log/monitord/
chmod 750 /opt/monitord/monitord.sh
chmod 750 /var/log/monitord/

# Enable the service via symlink (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/monitord.service /etc/systemd/system/multi-user.target.wants/monitord.service

# Start the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start monitord.service
fi
