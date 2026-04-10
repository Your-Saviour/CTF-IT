#!/bin/bash
set -e

pip3 install flask gunicorn
mkdir -p /opt/inventory/uploads
