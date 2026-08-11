#!/bin/bash
set -e

# Deliberately remove output encoding so the stored payload executes.
sed -i "s/htmlspecialchars(\$msg, ENT_QUOTES, 'UTF-8')/\$msg/" /opt/guestbook/index.php
echo "attacker: <script>alert('XSS')</script>" >> /opt/guestbook/messages.txt
