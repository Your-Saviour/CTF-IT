#!/bin/bash
set -e

# Install Node.js 20 LTS via NodeSource (idempotent — safe if notes_api already installed it)
if ! command -v node &>/dev/null || [[ "$(node --version)" != v20* ]]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi

# Create portal system user
useradd -r -s /bin/false portal 2>/dev/null || true

# Create application directory
mkdir -p /opt/portal
