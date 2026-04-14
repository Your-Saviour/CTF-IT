#!/bin/bash
set -e

# No-op: port 9000 is already open because the parent monitord service is running.
# This module documents the unauthenticated exposure as a standalone scoreable finding.
true
