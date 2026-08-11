#!/bin/bash
set -e
docker rm -f training-maintenance >/dev/null 2>&1 || true
docker run -d --name training-maintenance --privileged ubuntu:24.04 sleep infinity
