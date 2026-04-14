#!/bin/bash
set -e

sed -i 's/Options -Indexes/Options +Indexes/' /etc/apache2/sites-enabled/guestbook.conf
systemctl reload apache2 2>/dev/null || true
