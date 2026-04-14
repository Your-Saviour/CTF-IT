#!/bin/bash
set -e

# Enable hidden file exposure in directory listings
sed -i 's/show_hidden = false/show_hidden = true/' /opt/fileserver/config.toml

# Plant a hidden file to make the vuln obvious
echo "SECRET_API_KEY=ctf-flag-hidden-files-exposed" > /opt/fileserver/files/.env
chown fileserver:fileserver /opt/fileserver/files/.env

# Restart the service if systemd is running
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl restart fileserver
fi
