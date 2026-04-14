#!/bin/bash
set -e

sed -i 's/^User www-data/User root/' /etc/apache2/apache2.conf
sed -i 's/^Group www-data/Group root/' /etc/apache2/apache2.conf
systemctl restart apache2 2>/dev/null || true
