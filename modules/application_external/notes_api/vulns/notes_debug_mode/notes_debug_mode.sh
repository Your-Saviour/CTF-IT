#!/bin/bash
set -e

grep -q 'DEBUG=true' /etc/systemd/system/notesapi.service || \
    sed -i '/^RestartSec=5/a Environment=DEBUG=true' /etc/systemd/system/notesapi.service
