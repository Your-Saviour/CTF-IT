#!/bin/bash
# Setup script for Ubuntu 24.04 LTS Server base type
# Configures SSH and system-level settings for CTF environment

set -euo pipefail

echo "=== Ubuntu 24.04 LTS Server Base Setup ==="

# Create SSH runtime directory
mkdir -p /run/sshd

# Configure SSH to allow root login
echo "Configuring SSH..."
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
grep -q "^PermitRootLogin" /etc/ssh/sshd_config || echo "PermitRootLogin yes" >> /etc/ssh/sshd_config

# Configure password authentication
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
grep -q "^PasswordAuthentication" /etc/ssh/sshd_config || echo "PasswordAuthentication yes" >> /etc/ssh/sshd_config
echo "PasswordAuthentication yes" > /etc/ssh/sshd_config.d/ctf-override.conf

# Enable SSH service to start on boot
echo "Enabling SSH service..."
systemctl enable ssh.service 2>/dev/null || systemctl enable sshd.service || { echo "ERROR: could not enable SSH service"; exit 1; }

# Start SSH service
echo "Starting SSH service..."
systemctl start ssh.service 2>/dev/null || systemctl start sshd.service || { echo "ERROR: could not start SSH service"; exit 1; }

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
