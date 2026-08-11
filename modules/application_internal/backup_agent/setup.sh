#!/bin/bash
set -e
install -d -m 750 /opt/ctf-backup-agent /var/lib/ctf-backups
cat >/opt/ctf-backup-agent/run <<'EOF'
#!/bin/sh
while true; do date -Is > /var/lib/ctf-backups/last-run; sleep 60; done
EOF
chmod 750 /opt/ctf-backup-agent/run
cat >/etc/systemd/system/ctf-backup-agent.service <<'EOF'
[Unit]
Description=CTF backup agent
[Service]
ExecStart=/opt/ctf-backup-agent/run
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ctf-backup-agent
