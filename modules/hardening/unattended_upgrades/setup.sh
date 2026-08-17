#!/bin/bash
set -e

# Remove automatic security patching so the environment relies solely on
# manual upgrades until the blue team re-enables it.
rm -f /etc/apt/apt.conf.d/20auto-upgrades
rm -f /etc/apt/apt.conf.d/50unattended-upgrades

DEBIAN_FRONTEND=noninteractive apt-get purge -y unattended-upgrades 2>/dev/null || true

systemctl stop unattended-upgrades 2>/dev/null || true
systemctl disable unattended-upgrades 2>/dev/null || true
