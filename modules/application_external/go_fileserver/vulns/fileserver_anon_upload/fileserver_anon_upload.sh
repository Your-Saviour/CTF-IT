#!/bin/bash
set -e

# Enable anonymous file uploads
sed -i 's/allow_anonymous_upload = false/allow_anonymous_upload = true/' /opt/fileserver/config.toml

# Restart the service if systemd is running
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl restart fileserver
fi
