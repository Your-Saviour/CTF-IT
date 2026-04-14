#!/bin/bash
set -e

# Remove User directive from the service file so it runs as root
sed -i '/^User=fileserver/d' /etc/systemd/system/fileserver.service

# Reload and restart
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl restart fileserver
fi
