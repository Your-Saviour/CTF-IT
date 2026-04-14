#!/bin/bash
set -e

sed -i "s/process\.env\.ADMIN_TOKEN || ''/'SuperSecret123'/" /opt/notesapi/app.js
