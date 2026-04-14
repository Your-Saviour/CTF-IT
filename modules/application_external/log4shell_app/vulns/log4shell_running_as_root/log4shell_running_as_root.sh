#!/bin/bash
set -e

# Remove the User directive so the service runs as root
sed -i '/^User=logapp/d' /etc/systemd/system/logapp.service

systemctl daemon-reload
systemctl restart logapp.service
