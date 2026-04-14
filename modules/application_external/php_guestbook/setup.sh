#!/bin/bash
set -e

DEBIAN_FRONTEND=noninteractive apt-get install -y apache2 php libapache2-mod-php
mkdir -p /opt/guestbook
# Add Listen 8080 to ports.conf if not already present
grep -q "Listen 8080" /etc/apache2/ports.conf || echo "Listen 8080" >> /etc/apache2/ports.conf
a2enmod rewrite
