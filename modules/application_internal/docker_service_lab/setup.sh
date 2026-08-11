#!/bin/bash
set -e
apt-get update
apt-get install -y docker.io
systemctl enable --now docker
docker image inspect ubuntu:24.04 >/dev/null 2>&1 || docker pull ubuntu:24.04
