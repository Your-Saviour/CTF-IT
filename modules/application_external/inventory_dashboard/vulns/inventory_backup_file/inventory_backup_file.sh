#!/bin/bash
set -e

# Create a text backup of the database with a marker string
python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/inventory/inventory.db')
with open('/opt/inventory/inventory.db.bak', 'w') as f:
    for line in conn.iterdump():
        f.write(line + '\n')
    f.write('-- SENSITIVE_BACKUP_DATA: Contains user credentials and host inventory\n')
conn.close()
"
