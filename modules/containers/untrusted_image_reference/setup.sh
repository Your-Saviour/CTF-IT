#!/bin/bash
set -e
mkdir -p /opt/container-lab
cat >/opt/container-lab/compose.yaml <<'EOF'
services:
  worker:
    image: ubuntu:latest
    command: [sleep, infinity]
EOF
