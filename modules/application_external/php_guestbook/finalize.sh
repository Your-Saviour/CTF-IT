#!/bin/bash
set -e

a2ensite guestbook.conf
a2dissite 000-default.conf || true

# Set ownership and permissions
chown -R www-data:www-data /opt/guestbook/
chmod 750 /opt/guestbook/
chmod 640 /opt/guestbook/config.php
touch /opt/guestbook/messages.txt
chmod 664 /opt/guestbook/messages.txt
chown www-data:www-data /opt/guestbook/messages.txt

# Enable Apache to start on boot (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /lib/systemd/system/apache2.service /etc/systemd/system/multi-user.target.wants/apache2.service || true

# Start Apache if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl restart apache2
fi
