#!/bin/bash
set -e

# Detect architecture and install correct binary
ARCH=$(cat /opt/fileserver/.arch 2>/dev/null || uname -m)
case "$ARCH" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
esac

cp /opt/fileserver/bin/fileserver-linux-${ARCH} /opt/fileserver/fileserver
chmod 755 /opt/fileserver/fileserver
rm -rf /opt/fileserver/bin /opt/fileserver/.arch

# Create system user
if ! id -u fileserver &>/dev/null; then
    useradd -r -s /bin/false fileserver
fi

# Set ownership and permissions
chown -R fileserver:fileserver /opt/fileserver/
chmod 600 /opt/fileserver/server.key
chmod 644 /opt/fileserver/server.crt
chmod 640 /opt/fileserver/config.toml
chmod 755 /opt/fileserver/
chmod 755 /opt/fileserver/files/

# Enable the service (Docker-safe — no systemctl needed)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/fileserver.service \
    /etc/systemd/system/multi-user.target.wants/fileserver.service

# Start the service if systemd is available (VM provisioning)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start fileserver.service
fi
