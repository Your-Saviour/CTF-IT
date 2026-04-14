#!/bin/bash
set -e

# Enable path traversal by disabling path sanitisation
sed -i 's/sanitize_paths = true/sanitize_paths = false/' /opt/fileserver/config.toml

# Restart the service if systemd is running
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl restart fileserver
fi
