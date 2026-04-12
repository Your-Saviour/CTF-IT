#!/bin/bash
# Setup script for Ubuntu 24.04 LTS Server base type
# Configures SSH and system-level settings for CTF environment

set -euo pipefail

echo "=== Ubuntu 24.04 LTS Server Base Setup ==="

# Create SSH runtime directory
mkdir -p /run/sshd

# Configure SSH to allow root login
echo "Configuring SSH..."
if grep -q "^#PermitRootLogin" /etc/ssh/sshd_config; then
    sed -i 's/^#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
elif ! grep -q "^PermitRootLogin" /etc/ssh/sshd_config; then
    echo "PermitRootLogin yes" >> /etc/ssh/sshd_config
fi

# Enable SSH service to start on boot
echo "Enabling SSH service..."
systemctl enable ssh.service || systemctl enable sshd.service || true

# Start SSH service
echo "Starting SSH service..."
systemctl start ssh.service || systemctl start sshd.service || true

# Configure journald for production/container use
echo "Configuring journald..."
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/ctf.conf << 'EOF'
[Journal]
Storage=persistent
RuntimeMaxUse=256M
MaxRetentionSec=7day
EOF

# Configure core system limits for security testing
echo "Configuring system parameters..."
cat >> /etc/security/limits.conf << 'EOF'
# CTF environment limits
* soft nproc 65536
* hard nproc 65536
* soft nofile 65536
* hard nofile 65536
EOF

# Initialize syslog if needed
echo "Ensuring syslog is configured..."
systemctl enable rsyslog.service || true
systemctl start rsyslog.service || true

echo "=== Base setup complete ==="
