#!/bin/bash
set -e
apt-get update
apt-get install -y socat
cat >/etc/systemd/system/docker-tcp-proxy.service <<'EOF'
[Unit]
After=docker.service
[Service]
ExecStart=/usr/bin/socat TCP-LISTEN:2375,fork,reuseaddr UNIX-CONNECT:/var/run/docker.sock
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now docker-tcp-proxy
