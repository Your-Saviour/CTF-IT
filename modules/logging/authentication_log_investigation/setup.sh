#!/bin/bash
set -e
mkdir -p /root/incident-findings
rm -f /root/incident-findings/authentication-spray.txt
printf '%s\n' 'Aug 12 10:00:01 target sshd[4100]: Failed password for backupsvc from 198.51.100.24 port 42001 ssh2' 'Aug 12 10:00:04 target sshd[4104]: Failed password for backupsvc from 198.51.100.24 port 42005 ssh2' >> /var/log/auth.log
