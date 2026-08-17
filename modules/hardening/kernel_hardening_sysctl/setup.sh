#!/bin/bash
set -e

# Relax kernel hardening parameters so the blue team must restore them.
cat > /etc/sysctl.d/99-ctf-training.conf <<'EOF'
# Training baseline — kernel hardening deliberately relaxed.
kernel.kptr_restrict = 0
kernel.dmesg_restrict = 0
kernel.yama.ptrace_scope = 0
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
fs.suid_dumpable = 1
EOF

if command -v sysctl >/dev/null 2>&1; then
    sysctl --system >/dev/null 2>&1 || sysctl -p /etc/sysctl.d/99-ctf-training.conf >/dev/null 2>&1 || true
fi