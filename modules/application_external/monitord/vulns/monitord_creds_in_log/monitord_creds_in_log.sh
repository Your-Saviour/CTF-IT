#!/bin/bash
set -e

# Insert credential logging line after the "monitord starting" log line
sed -i '/log "monitord starting on port/a log "DB_PASSWORD=${DB_PASSWORD:-changeme456}"' /opt/monitord/monitord.sh
