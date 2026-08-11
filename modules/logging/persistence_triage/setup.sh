#!/bin/bash
set -e
mkdir -p /root/incident-findings
rm -f /root/incident-findings/persistence-triage.txt
printf '%s\n' '#!/bin/sh' 'date -Is >> /tmp/.profile-checkins' > /etc/profile.d/.system-update.sh
chmod 755 /etc/profile.d/.system-update.sh
