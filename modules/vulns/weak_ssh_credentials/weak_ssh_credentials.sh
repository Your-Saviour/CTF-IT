#!/bin/bash
# Create a system user with a weak password — Caldera's initial foothold.
# Stage: caldera — hidden from blue team.
useradd -m -s /bin/bash svc-monitor 2>/dev/null || true
echo "svc-monitor:monitor2024" | chpasswd
