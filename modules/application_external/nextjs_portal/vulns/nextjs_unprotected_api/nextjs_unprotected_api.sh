#!/bin/bash
set -e

# Remove the session authentication check from the admin users API route
python3 - <<'PYEOF'
path = '/opt/portal/app/api/admin/users/route.ts'
with open(path) as f:
    content = f.read()

content = content.replace("import { getServerSession } from 'next-auth'\n", "")
content = content.replace("import { authOptions } from '@/lib/auth'\n", "")
content = content.replace(
    "  const session = await getServerSession(authOptions)\n"
    "  if (!session) {\n"
    "    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })\n"
    "  }\n",
    ""
)

with open(path, 'w') as f:
    f.write(content)
PYEOF

# Rebuild the application to apply the change
cd /opt/portal && node_modules/.bin/next build

if systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; then
    systemctl restart portal
fi
