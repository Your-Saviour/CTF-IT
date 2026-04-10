#!/bin/bash
set -e

sed -i 's/app.secret_key = os.urandom(24).hex()/app.secret_key = "changeme"/' \
    /opt/inventory/app.py
