#!/usr/bin/env bash
set -euo pipefail

: "${CTF_DEPLOY_KEY_PATH:?CTF_DEPLOY_KEY_PATH is required}"
: "${CTF_DEPLOY_OUTPUT_DIR:?CTF_DEPLOY_OUTPUT_DIR is required}"
: "${EXPO_PRIVATE_URL:?EXPO_PRIVATE_URL is required}"
: "${EXPO_TEAMS:?EXPO_TEAMS is required}"

install -d -m 0755 /opt/expo-it
install -d -m 0700 "$CTF_DEPLOY_OUTPUT_DIR"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 git openssh-client openssl nginx

install -m 0600 /dev/null /opt/expo-it/github_known_hosts
ssh-keyscan -t rsa,ecdsa,ed25519 github.com > /opt/expo-it/github_known_hosts
export GIT_SSH_COMMAND="ssh -i $CTF_DEPLOY_KEY_PATH -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/opt/expo-it/github_known_hosts"
if [ ! -d /opt/expo-it/repository/.git ]; then
  git clone --branch stable --single-branch git@github.com:Your-Saviour/Expo-IT.git /opt/expo-it/repository
else
  git -C /opt/expo-it/repository fetch origin stable
  git -C /opt/expo-it/repository checkout --detach origin/stable
fi
resolved_commit=$(git -C /opt/expo-it/repository rev-parse HEAD)
session_secret=$(openssl rand -hex 32)
api_key=$(openssl rand -hex 32)
install -m 0600 /dev/null /opt/expo-it/repository/.env
{
  echo "EXPO_SESSION_SECRET=$session_secret"
  echo "EXPO_API_KEY=$api_key"
  echo "EXPO_TEAMS=$EXPO_TEAMS"
  echo "EXPO_COOKIE_SECURE=true"
} > /opt/expo-it/repository/.env
docker compose -f /opt/expo-it/repository/docker-compose.yml up -d --build
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=expo-it.gamenet" \
  -keyout /etc/ssl/private/expo-it.key -out /etc/ssl/certs/expo-it.crt
cat > /etc/nginx/sites-available/expo-it <<'EOF'
server {
  listen 443 ssl;
  ssl_certificate /etc/ssl/certs/expo-it.crt;
  ssl_certificate_key /etc/ssl/private/expo-it.key;
  location / { proxy_pass http://127.0.0.1:8080; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; }
}
EOF
ln -sf /etc/nginx/sites-available/expo-it /etc/nginx/sites-enabled/expo-it
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
printf '%s' "$api_key" > "$CTF_DEPLOY_OUTPUT_DIR/expo_it.api_key"
chmod 0600 "$CTF_DEPLOY_OUTPUT_DIR/expo_it.api_key"
printf '{"expo_it.resolved_commit":"%s","expo_it.private_url":"%s"}\n' "$resolved_commit" "$EXPO_PRIVATE_URL"
