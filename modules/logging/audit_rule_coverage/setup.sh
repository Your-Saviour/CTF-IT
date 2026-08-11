#!/bin/bash
set -e
apt-get update
apt-get install -y auditd
rm -f /etc/audit/rules.d/identity.rules
systemctl enable --now auditd || true
