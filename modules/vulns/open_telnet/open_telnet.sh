#!/bin/bash
apt-get install -y telnetd xinetd
systemctl enable xinetd
systemctl start xinetd
