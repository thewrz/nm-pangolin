#!/bin/bash
set -euo pipefail

# Install Python service files
sudo install -Dm755 src/nm_pangolin_service.py /usr/lib/nm-pangolin/nm-pangolin-service
sudo install -Dm644 src/pangolin_wrapper.py /usr/lib/nm-pangolin/pangolin_wrapper.py
sudo install -Dm644 src/config.py /usr/lib/nm-pangolin/config.py
sudo install -Dm644 src/__init__.py /usr/lib/nm-pangolin/__init__.py
sudo install -Dm644 src/__main__.py /usr/lib/nm-pangolin/__main__.py

# Install NM VPN plugin descriptor
sudo install -Dm644 conf/nm-pangolin.name /etc/NetworkManager/VPN/nm-pangolin.name

# Install D-Bus service activation file
sudo install -Dm644 conf/nm-pangolin-service.service /usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.pangolin.service

# Install D-Bus policy file
sudo install -Dm644 conf/nm-pangolin.conf /etc/dbus-1/system.d/nm-pangolin.conf

# Reload D-Bus and NetworkManager
sudo systemctl reload dbus
sudo systemctl restart NetworkManager

echo "Installed nm-pangolin service."
echo "Create a connection with: nmcli connection add type vpn vpn-type pangolin con-name 'Pangolin VPN'"
