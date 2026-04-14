#!/bin/bash
set -e

python3 - <<'PYEOF'
path = '/opt/notesapi/app.js'
with open(path) as f:
    content = f.read()
content = content.replace(
    "db.prepare('SELECT * FROM notes WHERE body LIKE ?').all('%' + term + '%')",
    """db.prepare("SELECT * FROM notes WHERE body LIKE '%" + req.query.q + "%'").all()"""
)
with open(path, 'w') as f:
    f.write(content)
PYEOF
