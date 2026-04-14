#!/bin/bash
set -e
# The vulnerability is the pinned Next.js 14.1.0 version installed by the parent module.
# CVE-2025-29927: x-middleware-subrequest header bypasses middleware auth entirely.
# This script is a no-op; the vulnerable version is already in place from package.json.
grep -q '"next": "14.1.0"' /opt/portal/package.json || true
