#!/bin/bash
set -e

python3 -c "
import sqlite3, hashlib
h = hashlib.sha256(b'admin').hexdigest()
conn = sqlite3.connect('/opt/inventory/inventory.db')
conn.execute('INSERT OR REPLACE INTO users (username, password) VALUES (?, ?)', ('admin', h))
conn.commit()
conn.close()
"
