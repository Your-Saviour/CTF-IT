#!/bin/bash
set -e

# Add a public /probe route that fetches an attacker-supplied URL server-side
# with no host allow-list, enabling SSRF against internal services.
# Guard the append so re-provisioning does not register the route twice.
if ! grep -q "@app.route('/probe')" /opt/inventory/app.py; then
cat >> /opt/inventory/app.py <<'EOF'


import urllib.request


@app.route('/probe')
def probe():
    target = request.args.get('target', '')
    if not target:
        return 'No target URL provided', 400
    try:
        with urllib.request.urlopen(target, timeout=5) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return 'Probe failed: {}'.format(e), 500
EOF
fi

# Restart the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl restart inventory.service
fi
