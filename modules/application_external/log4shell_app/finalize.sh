#!/bin/bash
set -e

# Create logapp system user
useradd -r -s /bin/false logapp 2>/dev/null || true

# Set ownership and permissions
chown -R logapp:logapp /opt/logapp/ /var/log/logapp/
chmod 750 /var/log/logapp/
chmod 640 /opt/logapp/application.properties

# Enable the service via symlink (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/logapp.service /etc/systemd/system/multi-user.target.wants/logapp.service

# Start the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start logapp.service
fi
