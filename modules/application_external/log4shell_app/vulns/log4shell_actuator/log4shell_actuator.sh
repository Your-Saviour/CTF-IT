#!/bin/bash
set -e

# Expose all Spring Boot Actuator endpoints (wildcard inclusion)
sed -i 's/management.endpoints.web.exposure.include=health/management.endpoints.web.exposure.include=*/' /opt/logapp/application.properties

systemctl restart logapp.service
