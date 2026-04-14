#!/bin/bash
set -e

# Enable production source maps in next.config.js
sed -i 's/const nextConfig = {}/const nextConfig = {\n  productionBrowserSourceMaps: true\n}/' /opt/portal/next.config.js

# Rebuild the application to generate .map files
cd /opt/portal && node_modules/.bin/next build

if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl restart portal
fi
