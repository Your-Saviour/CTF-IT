#!/bin/bash
set -e
mkdir -p /etc/ssh/sshd_config.d
printf '%s\n' 'AllowTcpForwarding yes' 'GatewayPorts yes' > /etc/ssh/sshd_config.d/90-remote-maintenance.conf
systemctl reload ssh.service || systemctl reload sshd.service
