#!/bin/bash
cat > /opt/maintenance.sh << 'SCRIPT'
#!/bin/bash
# System maintenance - cleanup temp files
find /tmp -type f -mtime +7 -delete
SCRIPT
chmod 777 /opt/maintenance.sh
echo "*/15 * * * * root /opt/maintenance.sh" >> /etc/crontab
