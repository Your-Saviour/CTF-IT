#!/bin/bash
set -e
cat >/etc/systemd/system/ctf-management.service <<'EOF'
[Service]
ExecStart=/usr/bin/python3 -m http.server 9443
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ctf-management
