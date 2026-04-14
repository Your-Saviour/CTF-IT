#!/bin/bash
set -e

# Install production dependencies
cd /opt/portal && npm install --omit=dev

# Build the Next.js application (60-90s on a small VPS)
cd /opt/portal && node_modules/.bin/next build

# Set ownership and permissions
chown -R portal:portal /opt/portal/
chmod 750 /opt/portal/

# Enable the service via symlink (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/portal.service /etc/systemd/system/multi-user.target.wants/portal.service

# Start the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start portal.service
fi
