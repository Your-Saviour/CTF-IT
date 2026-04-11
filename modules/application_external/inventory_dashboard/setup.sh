#!/bin/bash
set -e

pip3 install flask gunicorn --break-system-packages
mkdir -p /opt/inventory/uploads
