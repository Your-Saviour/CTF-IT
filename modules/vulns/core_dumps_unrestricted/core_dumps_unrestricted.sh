#!/bin/bash
# Remove core dump restriction from limits.conf
sed -i '/hard core 0/d' /etc/security/limits.conf

# Enable SUID core dumps via sysctl
grep -q "fs.suid_dumpable" /etc/sysctl.conf && \
  sed -i 's/^fs.suid_dumpable.*/fs.suid_dumpable = 2/' /etc/sysctl.conf || \
  echo "fs.suid_dumpable = 2" >> /etc/sysctl.conf
sysctl -p

# Set unlimited core dumps at runtime
ulimit -c unlimited
