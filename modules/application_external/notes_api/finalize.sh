#!/bin/bash
set -e

# Create notesapi system user
useradd -r -s /bin/false notesapi 2>/dev/null || true

# Install npm dependencies (package.json has been copied by this point)
cd /opt/notesapi && npm install --omit=dev

# Initialize the database (creates notes.db with schema at build time)
node -e "
const Database = require('/opt/notesapi/node_modules/better-sqlite3');
const db = new Database('/opt/notesapi/notes.db');
db.exec(\`
  CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
  );
  INSERT OR IGNORE INTO users (username, password) VALUES ('admin', 'adminpass');
  INSERT OR IGNORE INTO notes (title, body) VALUES ('Welcome', 'Welcome to the Notes API');
\`);
db.close();
"

# Set ownership and permissions
chown -R notesapi:notesapi /opt/notesapi/
chmod 750 /opt/notesapi/
chmod 640 /opt/notesapi/notes.db

# Enable the service via symlink (safe during Docker builds)
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/notesapi.service /etc/systemd/system/multi-user.target.wants/notesapi.service

# Start the service if systemd is running (VM provisioning; skipped during Docker builds)
if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl daemon-reload
    systemctl start notesapi.service
fi
