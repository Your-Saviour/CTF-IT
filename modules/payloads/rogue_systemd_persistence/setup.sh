#!/bin/bash
set -e
install -d /var/lib/ctf-markers
cat >/usr/local/bin/system-health-sync <<'EOF'
#!/bin/sh
while true; do date -Is > /var/lib/ctf-markers/system-health-sync; sleep 30; done
EOF
chmod 755 /usr/local/bin/system-health-sync
cat >/etc/systemd/system/system-health-sync.service <<'EOF'
[Unit]
Description=System health synchronisation
[Service]
ExecStart=/usr/local/bin/system-health-sync
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now system-health-sync.service
