#!/bin/bash
set -e

python3 /tmp/init_db.py && rm /tmp/init_db.py

# Enable the service via symlink (systemctl not available during build)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/inventory.service /etc/systemd/system/multi-user.target.wants/inventory.service
