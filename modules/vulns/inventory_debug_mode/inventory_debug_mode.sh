#!/bin/bash
set -e

grep -q 'FLASK_DEBUG=1' /etc/systemd/system/inventory.service || \
    sed -i '/^Restart=always/a Environment=FLASK_DEBUG=1' \
        /etc/systemd/system/inventory.service
