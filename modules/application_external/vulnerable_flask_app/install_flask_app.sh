#!/bin/bash
set -e

apt-get install -y python3-venv
mkdir -p /opt/flaskapp
python3 -m venv /opt/flaskapp/venv
/opt/flaskapp/venv/bin/pip install flask gunicorn
cat > /opt/flaskapp/app.py << 'PYEOF'
from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "<h1>Welcome to the CTF App</h1>"

@app.route("/admin")
def admin():
    return "<h1>Admin Panel</h1><p>Nothing to see here.</p>"
PYEOF

cat > /etc/systemd/system/flaskapp.service << 'SVCEOF'
[Unit]
Description=CTF Flask Application
After=network.target

[Service]
ExecStart=/opt/flaskapp/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
WorkingDirectory=/opt/flaskapp
Restart=always

[Install]
WantedBy=multi-user.target
SVCEOF

# Enable the service via symlink (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/flaskapp.service /etc/systemd/system/multi-user.target.wants/flaskapp.service

# Start the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start flaskapp.service
fi
