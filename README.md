# CTF-IT

A CTF training platform where each user gets a uniquely generated Docker container with randomized vulnerabilities and hardening tasks. Users fix issues inside their container, then submit verification to the platform for scoring.

## Quick Start

### Prerequisites

- Docker with Docker Compose
- Python 3.12+

### 1. Build the base image

```bash
cd base
source .env
docker build --build-arg ROOT_PASSWORD=$ROOT_PASSWORD -t ctf-base:latest .
```

### 2. Set environment variables

Create a `.env` file in the project root:

```
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:///ctf.db
EVENT_QUOTA={"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}
```

### 3. Start the platform

```bash
docker-compose up -d
```

The API will be available at `http://localhost:8000`.

## How It Works

1. User registers on the platform
2. A Docker image is built in the background with randomly selected modules based on the event quota
3. User pulls and runs their container
4. User fixes vulnerabilities and implements hardening tasks inside the container
5. User runs `collect.py` inside the container to gather system state
6. Results are submitted to `/api/verify` for scoring

## Project Structure

```
CTF-IT/
  api/                  # FastAPI application
    routes/             # auth, images, verify, scoreboard, admin
    models.py           # User, UserImage, UserModule, Event
    main.py             # App entry point
  base/                 # Base Docker image (Ubuntu 22.04 + systemd)
  builder/              # Image build orchestration
    main.py             # Build entry point
    selector.py         # Module selection with quota/conflicts/deps
    renderer.py         # Dockerfile + manifest generation
    module_loader.py    # YAML module parsing
  modules/              # Module definitions
    vulns/              # Vulnerability modules (one folder per module)
    hardening/          # Hardening modules (one folder per module)
  templates/
    Dockerfile.j2       # Jinja2 template for user images
  frontend/
    templates/          # Jinja2 HTML templates
  collect.py            # In-container verification script
  docker-compose.yml    # Production deployment
  requirements.txt      # Python dependencies
```

## Modules

Modules are self-contained YAML definitions with optional shell scripts. See [MODULE_GUIDE.md](MODULE_GUIDE.md) for a complete guide on creating new modules.

Each module lives in its own folder:

```
modules/
  vulns/
    world_writable_shadow/
      world_writable_shadow.yaml
      world_writable_shadow.sh
    suid_find/
      suid_find.yaml
      suid_find.sh
    writable_cron_script/
      writable_cron_script.yaml
      writable_cron_script.sh
    nopasswd_sudo/
      nopasswd_sudo.yaml
      nopasswd_sudo.sh
    unauthorized_ssh_key/
      unauthorized_ssh_key.yaml
      unauthorized_ssh_key.sh
  hardening/
    change_root_password/
      change_root_password.yaml
    disable_ssh_root_login/
      disable_ssh_root_login.yaml
    install_fail2ban/
      install_fail2ban.yaml
    setup_ssh_key_auth/
      setup_ssh_key_auth.yaml
```

## Running a User Container

User containers require specific Docker flags for systemd support:

```bash
docker run -d \
  --cap-add SYS_ADMIN --cap-add NET_ADMIN --cgroupns=private \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  -p 2222:22 <image-tag>
```

Connect via SSH:

```bash
ssh root@localhost -p 2222
```

## Key Design Decisions

- **Deterministic flags**: `HMAC(SECRET_KEY, user_id)` ensures the same user always gets the same flag, enabling rebuilds without storing flags separately
- **Stateless verification**: the flag in the payload proves container legitimacy, no session required
- **Modular content**: adding a new module is typically just a YAML file and optional shell script — no code changes needed unless introducing a new verification type

## Testing

See [TEST_PLAN.md](TEST_PLAN.md) for the full end-to-end integration test. Copy `.env.test` to `.env` to use a test configuration that selects all modules.
