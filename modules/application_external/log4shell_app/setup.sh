#!/bin/bash
set -e

# Install Java JDK and Maven (needed to build the JAR on this machine)
apt-get install -y openjdk-17-jdk maven

# Create application and log directories
mkdir -p /opt/logapp
mkdir -p /var/log/logapp
