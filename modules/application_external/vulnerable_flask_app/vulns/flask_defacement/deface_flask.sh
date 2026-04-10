#!/bin/bash
set -e

# Inject defacement into the Flask app's index route
sed -i 's|<h1>Welcome to the CTF App</h1>|<h1>Welcome to the CTF App</h1><p>HACKED BY L33THAX0R</p>|' \
    /opt/flaskapp/app.py
