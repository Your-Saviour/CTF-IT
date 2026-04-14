#!/bin/bash
set -e

# Remove the Log4Shell mitigation flag from the service unit to reintroduce CVE-2021-44228
sed -i 's/ -Dlog4j2.formatMsgNoLookups=true//' /etc/systemd/system/logapp.service

systemctl daemon-reload
systemctl restart logapp.service
