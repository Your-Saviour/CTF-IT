#!/bin/bash
set -e

# Create directories
mkdir -p /opt/fileserver/files
mkdir -p /opt/fileserver/bin

# Detect architecture
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  ARCH="amd64" ;;
    aarch64) ARCH="arm64" ;;
    *)
        echo "Unsupported architecture: $ARCH"
        exit 1
        ;;
esac
echo "Detected architecture: $ARCH"
echo "$ARCH" > /opt/fileserver/.arch

# Generate self-signed TLS certificate and key
openssl req -x509 -newkey rsa:2048 \
    -keyout /opt/fileserver/server.key \
    -out /opt/fileserver/server.crt \
    -days 3650 -nodes \
    -subj "/CN=fileserver/O=CTF/C=US" \
    -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" 2>/dev/null

# Create sample files
cat > /opt/fileserver/files/readme.txt <<'EOF'
Welcome to the Go File Server.
This server hosts files over HTTPS on port 8000.
Browse the /files/ directory to see available files.
EOF

cat > /opt/fileserver/files/sample.csv <<'EOF'
id,name,value
1,alpha,100
2,beta,200
3,gamma,300
EOF
