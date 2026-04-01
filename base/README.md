# CTF Base Image

Ubuntu 22.04 image with systemd as PID 1, designed to feel like a standard Linux machine.

## What's included

- **systemd** as init (PID 1) with unnecessary container units masked
- **Services**: OpenSSH server, cron, rsyslog (all managed by systemd)
- **Firewall**: ufw, iptables
- **Editors**: vim, nano
- **Networking**: curl, wget, net-tools, iproute2, nmap, tcpdump, dnsutils, whois
- **Languages**: python3, pip
- **Utilities**: sudo, procps, lsof, find, file, less, git, unzip, tar, gzip, logrotate, man-db, ca-certificates

## Build

```bash
cp .env.example .env   # edit if you want a different root password
docker build --build-arg $(cat .env) -t ctf-base:latest .
```

The `.env` file contains `ROOT_PASSWORD` used for the root account. See `.env.example` for the default.

## Run

```bash
docker run -d --cap-add SYS_ADMIN --cap-add NET_ADMIN --cgroupns=private \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  -p 2222:22 ctf-base:latest
```

### Flags explained

| Flag | Why |
|------|-----|
| `--cap-add SYS_ADMIN` | systemd needs this to manage cgroups and mounts |
| `--cap-add NET_ADMIN` | Required for ufw/iptables firewall rules |
| `--cgroupns=private` | Isolates the container's cgroup namespace from the host |
| `-v /sys/fs/cgroup:...` | systemd needs read-write access to the cgroup filesystem |
| `--tmpfs /run /run/lock /tmp` | RAM-backed filesystems, same as a real Linux system |

## Connect

```bash
ssh root@ctf.exercise.blueteam.au -p 2222
```

The domain `ctf.exercise.blueteam.au` resolves to `127.0.0.1` via public DNS, so it connects to the container on your local machine with no extra setup.

Password is whatever `ROOT_PASSWORD` was set to in `.env`.
