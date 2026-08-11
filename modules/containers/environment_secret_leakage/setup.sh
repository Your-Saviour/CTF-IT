#!/bin/bash
set -e
mkdir -p /opt/container-lab/secrets
printf '%s\n' 'MODE=training' 'BACKUP_API_TOKEN=training-leaked-token-rotate-me' > /opt/container-lab/service.env
printf '%s\n' 'replace-this-value' > /opt/container-lab/secrets/backup_api_token
chmod 644 /opt/container-lab/secrets/backup_api_token
