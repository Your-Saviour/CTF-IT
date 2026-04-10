import sqlite3
conn = sqlite3.connect('/opt/inventory/inventory.db')
conn.executescript('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    os_type TEXT NOT NULL,
    status TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
INSERT INTO hosts (hostname, ip_address, os_type, status, last_seen) VALUES
    ('web-prod-01', '10.0.1.10', 'Ubuntu 22.04', 'online', '2026-04-06 08:00:00'),
    ('db-prod-01', '10.0.1.20', 'Ubuntu 22.04', 'online', '2026-04-06 07:55:00'),
    ('cache-prod-01', '10.0.1.30', 'Debian 12', 'offline', '2026-04-05 23:10:00'),
    ('app-staging-01', '10.0.2.10', 'Ubuntu 22.04', 'online', '2026-04-06 07:58:00'),
    ('monitor-01', '10.0.3.10', 'CentOS 9', 'online', '2026-04-06 08:01:00');
''')
conn.close()
