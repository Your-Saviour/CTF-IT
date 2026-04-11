#!/bin/bash
set -e

apt-get install -y python3-venv
mkdir -p /opt/inventory
python3 -m venv /opt/inventory/venv
/opt/inventory/venv/bin/pip install flask gunicorn
mkdir -p /opt/inventory/uploads
