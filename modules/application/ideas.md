# Application Module Ideas

## 1. Node.js Notes API

A simple Express.js REST API for storing notes, running on port 3000 as a systemd service. Stores data in a SQLite file at `/opt/notesapi/notes.db`.

**Vulnerabilities:**

1. **SQL Injection in Search** — The `/search?q=` endpoint concatenates user input directly into a SQL query. Fix: use parameterised queries.
   - Verification: `file_not_contains` checking `app.js` for raw string concatenation pattern

2. **World-Readable Database** — `notes.db` is chmod 666, letting any user dump all notes. Fix: restrict to 640 owned by the service user.
   - Verification: `file_permissions` on `/opt/notesapi/notes.db` expecting `640`

3. **Hardcoded Admin Token** — A plaintext API token `admin:SuperSecret123` is embedded in `app.js` and grants full access. Fix: remove the hardcoded token and use environment variable auth.
   - Verification: `file_not_contains` checking `app.js` for `SuperSecret123`

4. **Debug Mode Enabled** — The app runs with `DEBUG=true` exposing stack traces and internal paths in error responses. Fix: set `NODE_ENV=production` in the service file and remove the debug flag.
   - Verification: `file_not_contains` checking the systemd unit for `DEBUG=true`

5. **No Rate Limiting on Auth** — The `/login` endpoint has no rate limiting, allowing brute force. Fix: install and configure `express-rate-limit` middleware (config file check).
   - Verification: `file_contains` checking `app.js` for `rateLimit` or `rate-limit`

---

## 2. PHP Guestbook

A classic PHP guestbook app served by Apache on port 8080. Visitors can leave messages stored in a flat file at `/opt/guestbook/messages.txt`.

**Vulnerabilities:**

1. **Stored XSS** — User messages are rendered without escaping, allowing script injection. The build script injects a `<script>` tag into the messages file. Fix: remove the malicious entry and add `htmlspecialchars()` to the output code.
   - Verification: `file_not_contains` checking `messages.txt` for `<script>`

2. **Directory Listing Enabled** — Apache has `Options +Indexes` on the guestbook directory, exposing all files including config. Fix: set `Options -Indexes` in the Apache site config.
   - Verification: `file_not_contains` checking `/etc/apache2/sites-enabled/guestbook.conf` for `+Indexes`

3. **World-Writable Config** — `config.php` (containing DB path and admin password) is chmod 777. Fix: restrict to 640.
   - Verification: `file_permissions` on `/opt/guestbook/config.php` expecting `640`

4. **Exposed phpinfo()** — A `phpinfo.php` file exists in the web root, leaking server configuration. Fix: delete the file.
   - Verification: `http_response` on port 8080 with `body_not_contains: "phpinfo()"` (or check file absence via permissions)

5. **Apache Running as Root** — The Apache service is configured with `User root` instead of `www-data`. Fix: restore `User www-data` and `Group www-data` in the Apache config.
   - Verification: `file_not_contains` checking `/etc/apache2/apache2.conf` for `User root`

---

## 3. Python Inventory Dashboard

A Flask dashboard for managing server inventory, running on port 5001 via gunicorn. Has a simple login page and displays host information. Uses SQLite at `/opt/inventory/inventory.db`.

**Vulnerabilities:**

1. **Default Admin Credentials** — The app ships with `admin:admin` as the login. The build script inserts this into the users table. Fix: change the admin password via the app's CLI tool or direct DB update.
   - Verification: `http_response` on port 5001 checking `body_not_contains: "Default credentials detected"` (app shows a warning banner when default creds exist)

2. **Secret Key in Source Code** — `app.py` has `SECRET_KEY = "changeme"` hardcoded, making session cookies forgeable. Fix: replace with a random value or read from environment.
   - Verification: `file_not_contains` checking `/opt/inventory/app.py` for `changeme`

3. **Backup File in Web Root** — A `inventory.db.bak` backup with sensitive data sits in the web-accessible directory. Fix: delete it or move it outside the web root.
   - Verification: `file_permissions` on `/opt/inventory/inventory.db.bak` (expect file to not exist, verified by permission check returning empty)

4. **Verbose Error Pages** — The app runs with `FLASK_DEBUG=1` in the systemd environment, exposing the Werkzeug debugger with code execution. Fix: remove the debug flag from the service file.
   - Verification: `file_not_contains` checking the systemd unit for `FLASK_DEBUG=1`

5. **Unrestricted File Upload** — The `/upload` endpoint accepts any file type with no size limit, writing to a publicly accessible directory. The build script uploads a PHP shell. Fix: remove the shell and restrict uploads to safe file types in the code.
   - Verification: `file_not_contains` checking `/opt/inventory/uploads/` for `.php` files (or `http_response` body check)

---

## 4. Bash Monitoring Daemon

A custom shell-script daemon (`monitord`) that runs as a systemd service, collecting system metrics every 60 seconds and writing them to `/var/log/monitord/`. It also exposes a basic status page via `netcat` on port 9000.

**Vulnerabilities:**

1. **Command Injection in Hostname** — The script reads `/etc/hostname` and passes it unsanitised into a `eval`-based string. A crafted hostname value triggers arbitrary command execution. Fix: remove the `eval` and use proper variable quoting.
   - Verification: `file_not_contains` checking `/opt/monitord/monitord.sh` for `eval`

2. **World-Writable Log Directory** — `/var/log/monitord/` is chmod 777, allowing any user to tamper with or plant fake logs. Fix: restrict to 750 owned by root.
   - Verification: `file_permissions` on `/var/log/monitord` expecting `750`

3. **Credentials in Log Output** — The daemon logs the DB connection string (including password) in plaintext to its log file. The build script seeds a fake credential line. Fix: remove credential logging from the script and purge existing log entries.
   - Verification: `file_not_contains` checking `/opt/monitord/monitord.sh` for `DB_PASSWORD`

4. **SUID Bit on Daemon Script** — The daemon script has the SUID bit set, allowing any user to run it as root. Fix: remove the SUID bit.
   - Verification: `file_permissions` on `/opt/monitord/monitord.sh` expecting `750`

5. **Netcat Listener With No Auth** — The status page on port 9000 serves full system metrics (memory, disk, processes) to anyone. Fix: close port 9000 by removing the netcat listener from the script or firewall it.
   - Verification: `port_closed` on port 9000

---

## 5. Go File Server

A small Go binary serving static files from `/srv/files/` on port 8000. Used as an internal file-sharing tool. Runs as a systemd service under a `fileserver` user.

**Vulnerabilities:**

1. **Path Traversal** — The server doesn't sanitise `../` in request paths, allowing access to any file on the filesystem (e.g., `/etc/shadow`). The build script places a config file that disables path sanitisation. Fix: enable the `sanitize_paths` option in `/opt/fileserver/config.toml`.
   - Verification: `file_contains` checking `/opt/fileserver/config.toml` for `sanitize_paths = true`

2. **TLS Certificate with Default Key** — The server ships with a self-signed cert whose private key is world-readable at `/opt/fileserver/server.key`. Fix: restrict permissions to 600.
   - Verification: `file_permissions` on `/opt/fileserver/server.key` expecting `600`

3. **Directory Listing Exposes Hidden Files** — The config has `show_hidden = true`, exposing `.env`, `.git`, and other dotfiles. Fix: set `show_hidden = false`.
   - Verification: `file_not_contains` checking `/opt/fileserver/config.toml` for `show_hidden = true`

4. **Upload Endpoint Without Auth** — The `/upload` route is enabled with `allow_anonymous_upload = true`, letting anyone write files to the server. Fix: disable anonymous uploads in the config.
   - Verification: `file_not_contains` checking `/opt/fileserver/config.toml` for `allow_anonymous_upload = true`

5. **Service Running as Root** — The systemd unit runs the binary as root instead of the dedicated `fileserver` user. Fix: set `User=fileserver` and `Group=fileserver` in the unit file.
   - Verification: `file_contains` checking the systemd unit for `User=fileserver`

---

## 6. Java Log Management App (Log4Shell — CVE-2021-44228)

A small Java Spring Boot application that acts as a log aggregator dashboard, running on port 8081 via a fat JAR. Accepts log submissions via a POST endpoint and displays them in a web UI. Deliberately ships with **Log4j 2.14.1** (the vulnerable version). The app logs all incoming request headers using Log4j, making the `User-Agent` or any custom header a direct injection point for JNDI lookups (`${jndi:ldap://...}`).

The install script downloads a pre-built JAR (or builds from a vendored Maven project in the module directory), installs a JRE, creates a `logapp` service user, and runs it as a systemd service.

**Vulnerabilities:**

1. **Log4Shell (CVE-2021-44228)** — The app uses Log4j 2.14.1 which evaluates JNDI lookup strings in log messages, enabling remote code execution. Fix: upgrade Log4j to 2.17.1+ by replacing the JAR, or set the JVM flag `-Dlog4j2.formatMsgNoLookups=true` in the systemd unit.
   - Verification: `file_contains` checking the systemd unit `ExecStart` line for `formatMsgNoLookups=true`, OR `file_not_contains` checking `/opt/logapp/lib/` for `log4j-core-2.14` (verifying the vulnerable JAR was replaced)

2. **JNDI Lookup Not Globally Disabled** — Even after patching Log4j, the JVM-wide JNDI can still be exploited by other libraries. Fix: set `-Dcom.sun.jndi.ldap.object.trustURLCodebase=false` in the service file.
   - Verification: `file_contains` checking the systemd unit for `trustURLCodebase=false`

3. **App Runs as Root** — The systemd unit runs the JAR as root rather than the dedicated `logapp` user. If Log4Shell is exploited, the attacker gets root immediately. Fix: set `User=logapp` in the unit file.
   - Verification: `file_contains` checking the systemd unit for `User=logapp`

4. **Sensitive Logs World-Readable** — Log files at `/var/log/logapp/` are chmod 755, exposing submitted log data (which may contain tokens, IPs, etc.) to all users. Fix: restrict to 750 owned by `logapp`.
   - Verification: `file_permissions` on `/var/log/logapp` expecting `750`

5. **Management Endpoint Exposed** — Spring Boot Actuator is enabled with all endpoints exposed on the same port (`management.endpoints.web.exposure.include=*` in `application.properties`), leaking environment variables, heap dumps, and thread info. Fix: disable or restrict actuator endpoints.
   - Verification: `file_not_contains` checking `/opt/logapp/application.properties` for `exposure.include=*`

---

## 7. Next.js Employee Portal (Middleware Auth Bypass — CVE-2025-29927)

A Next.js 14.1.0 application serving as an employee portal on port 3001. It has a public login page, an authenticated `/dashboard` with employee records, and an `/admin` panel. Authentication is enforced via Next.js middleware (`middleware.ts`) that checks session cookies and redirects unauthenticated users. The app ships with **Next.js 14.1.0** which is vulnerable to CVE-2025-29927 — an attacker can add the `x-middleware-subrequest` header with the middleware's module path to skip middleware execution entirely, bypassing all authentication and accessing `/admin` or `/dashboard` directly.

The install script installs Node.js 20 LTS, copies the pre-built app to `/opt/portal/`, and runs it as a systemd service via `next start`.

**Vulnerabilities:**

1. **Middleware Auth Bypass (CVE-2025-29927)** — Next.js 14.1.0 allows requests with the internal `x-middleware-subrequest` header to bypass middleware entirely. Any route protected only by middleware (including `/admin`) is accessible without authentication. Fix: upgrade Next.js to 14.2.25+ by running the provided upgrade script, or add a server-side auth check that doesn't rely solely on middleware.
   - Verification: `file_not_contains` checking `/opt/portal/package.json` for `"next": "14.1.0"` (confirming the vulnerable version was upgraded)

2. **Admin API Route Unprotected** — The `/api/admin/users` API route relies entirely on middleware for auth (no server-side session check in the route handler). Even after patching CVE-2025-29927, any future middleware bypass would re-expose it. Fix: add `getServerSession()` check inside the route handler itself.
   - Verification: `file_contains` checking `/opt/portal/app/api/admin/users/route.ts` for `getServerSession`

3. **Employee Data in Client Bundle** — The dashboard page fetches employee records client-side from a public API route with no auth, meaning the data is accessible to anyone who knows the URL. Fix: move the data fetch to a server component and protect the API route.
   - Verification: `file_not_contains` checking `/opt/portal/app/api/employees/route.ts` for `export async function GET` without auth (or check for session validation)

4. **Debug Mode / Source Maps Exposed** — The app is built with `productionBrowserSourceMaps: true` in `next.config.js`, shipping full source maps that expose server-side logic and secret paths. Fix: set `productionBrowserSourceMaps: false` or remove the line.
   - Verification: `file_not_contains` checking `/opt/portal/next.config.js` for `productionBrowserSourceMaps: true`

5. **Session Secret Hardcoded** — The `NEXTAUTH_SECRET` is hardcoded as `"super-secret-dev-key"` in the systemd unit's `Environment=` line, making session tokens forgeable. Fix: replace with a strong random value.
   - Verification: `file_not_contains` checking the systemd unit for `super-secret-dev-key`
