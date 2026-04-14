#!/bin/bash
set -e

# phpinfo.php is already present at /opt/guestbook/phpinfo.php (copied by the parent module).
# This script is a no-op — the vulnerability is the file's presence.
true
