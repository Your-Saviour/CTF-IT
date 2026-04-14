#!/bin/bash
set -e

# Create application directories (bash and netcat are pre-installed in base image)
mkdir -p /opt/monitord
mkdir -p /var/log/monitord
