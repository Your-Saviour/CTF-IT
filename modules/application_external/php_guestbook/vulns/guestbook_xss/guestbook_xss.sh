#!/bin/bash
set -e

echo "attacker: <script>alert('XSS')</script>" >> /opt/guestbook/messages.txt
