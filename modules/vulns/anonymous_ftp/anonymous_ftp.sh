#!/bin/bash
apt-get install -y vsftpd
sed -i 's/^anonymous_enable=NO/anonymous_enable=YES/' /etc/vsftpd.conf
# Ensure the line exists if it wasn't there
grep -q "anonymous_enable" /etc/vsftpd.conf || echo "anonymous_enable=YES" >> /etc/vsftpd.conf
systemctl enable vsftpd
systemctl restart vsftpd
