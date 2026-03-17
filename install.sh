#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

trap 'echo ""; echo "Install interrupted. Check installed files manually or re-run."' ERR

# --- Privilege escalation ---
# Re-exec entire script as root to avoid per-command auth issues

if [[ $EUID -ne 0 ]]; then
    echo "This script needs root privileges."

    if command -v sudo &>/dev/null; then
        exec sudo bash "$0" "$@"
    elif command -v doas &>/dev/null; then
        exec doas bash "$0" "$@"
    elif command -v pkexec &>/dev/null; then
        exec pkexec bash "$0" "$@"
    fi

    echo "Error: No way to escalate privileges (tried sudo, doas, pkexec)."
    echo "Run this script as root."
    exit 1
fi

# --- Package manager detection ---

detect_pkg_manager() {
    if command -v pacman &>/dev/null; then
        echo "pacman"
    elif command -v apt-get &>/dev/null; then
        echo "apt"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    elif command -v zypper &>/dev/null; then
        echo "zypper"
    else
        echo "unknown"
    fi
}

PKG_MANAGER="$(detect_pkg_manager)"

pkg_install() {
    local packages=("$@")

    case "$PKG_MANAGER" in
        pacman)
            pacman -S --needed --noconfirm "${packages[@]}"
            ;;
        apt)
            apt-get update
            apt-get install -y "${packages[@]}"
            ;;
        dnf)
            dnf install -y "${packages[@]}"
            ;;
        zypper)
            zypper install -y "${packages[@]}"
            ;;
        *)
            echo "Unknown package manager. Please install manually:"
            echo "  ${packages[*]}"
            return 1
            ;;
    esac
}

service_deps() {
    case "$PKG_MANAGER" in
        pacman)  echo "python python-dbus python-gobject" ;;
        apt)     echo "python3 python3-dbus python3-gi" ;;
        dnf)     echo "python3 python3-dbus python3-gobject" ;;
        zypper)  echo "python3 python3-dbus-python python3-gobject" ;;
        *)       echo "python3 dbus-python pygobject" ;;
    esac
}

plasma_deps() {
    case "$PKG_MANAGER" in
        pacman)  echo "plasma-nm extra-cmake-modules qt6-base kf6-networkmanager-qt kf6-coreaddons kf6-i18n kf6-widgetsaddons cmake gcc make" ;;
        apt)     echo "cmake extra-cmake-modules qt6-base-dev build-essential libgl-dev libkf6networkmanagerqt-dev libkf6coreaddons-dev libkf6i18n-dev libkf6kwidgetsaddons-dev" ;;
        dnf)     echo "cmake extra-cmake-modules qt6-qtbase-devel gcc-c++ kf6-networkmanager-qt-devel kf6-kcoreaddons-devel kf6-ki18n-devel kf6-kwidgetsaddons-devel plasma-nm-devel" ;;
        zypper)  echo "cmake extra-cmake-modules qt6-base-devel gcc-c++ kf6-networkmanager-qt-devel kf6-kcoreaddons-devel kf6-ki18n-devel kf6-kwidgetsaddons-devel" ;;
        *)       echo "cmake extra-cmake-modules qt6-base kf6-networkmanager-qt" ;;
    esac
}

prompt_install() {
    local label="$1"
    shift
    local packages=("$@")

    echo ""
    echo "Missing $label dependencies. The following packages are needed:"
    echo "  ${packages[*]}"
    echo ""
    read -rp "Install them now? [Y/n] " answer || answer="n"
    answer="${answer:-Y}"

    if [[ "$answer" =~ ^[Yy] ]]; then
        pkg_install "${packages[@]}"
    else
        echo "Skipping. You can install them manually and re-run this script."
        return 1
    fi
}

# --- Check pangolin CLI ---

if ! command -v pangolin &>/dev/null; then
    echo "Warning: pangolin CLI not found."
    echo "The VPN service requires the pangolin binary to function."
    echo "Install it before attempting to connect (AUR: pangolin-bin, or manual install)."
    echo ""
fi

# --- Service dependencies ---

check_service_deps() {
    python3 -c "import dbus; from gi.repository import GLib" 2>/dev/null
}

if ! check_service_deps; then
    IFS=' ' read -ra deps <<< "$(service_deps)"
    if ! prompt_install "service" "${deps[@]}"; then
        echo "Cannot install service without dependencies."
        exit 1
    fi
fi

# --- Install Python D-Bus service ---

echo ""
echo "Installing NM Pangolin VPN service..."

install -Dm755 "$SCRIPT_DIR/src/nm_pangolin_service.py" /usr/lib/nm-pangolin/nm-pangolin-service
install -Dm644 "$SCRIPT_DIR/src/pangolin_wrapper.py" /usr/lib/nm-pangolin/pangolin_wrapper.py
install -Dm644 "$SCRIPT_DIR/src/config.py" /usr/lib/nm-pangolin/config.py
install -Dm644 "$SCRIPT_DIR/src/__init__.py" /usr/lib/nm-pangolin/__init__.py
install -Dm644 "$SCRIPT_DIR/src/__main__.py" /usr/lib/nm-pangolin/__main__.py

install -Dm644 "$SCRIPT_DIR/conf/nm-pangolin.name" /etc/NetworkManager/VPN/nm-pangolin.name
install -Dm644 "$SCRIPT_DIR/conf/nm-pangolin-service.service" /usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.pangolin.service
install -Dm644 "$SCRIPT_DIR/conf/nm-pangolin.conf" /etc/dbus-1/system.d/nm-pangolin.conf

systemctl restart NetworkManager

echo "Service installed."

# --- KDE Plasma plugin ---

echo ""
read -rp "Install KDE Plasma GUI plugin? [Y/n] " answer || answer="n"
answer="${answer:-Y}"

if [[ ! "$answer" =~ ^[Yy] ]]; then
    echo ""
    echo "Done. Create a connection with:"
    echo "  nmcli connection add type vpn vpn-type pangolin con-name 'Pangolin VPN'"
    exit 0
fi

# Offer to install all plasma build deps upfront
IFS=' ' read -ra deps <<< "$(plasma_deps)"
prompt_install "Plasma plugin build" "${deps[@]}" || true

PLUGIN_DIR="$SCRIPT_DIR/plasma-plugin"
BUILD_DIR="$PLUGIN_DIR/build"

echo ""
echo "Building Plasma plugin..."

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

if ! cmake "$PLUGIN_DIR" -DCMAKE_INSTALL_PREFIX=/usr 2>&1; then
    echo ""
    echo "CMake configure failed. Missing build dependencies."
    echo "Install them manually and re-run this script."
    echo ""
    echo "Service is installed. Create a connection with:"
    echo "  nmcli connection add type vpn vpn-type pangolin con-name 'Pangolin VPN'"
    exit 0
fi

if ! make -j"$(nproc)" 2>&1; then
    echo ""
    echo "Plasma plugin build failed. Check the errors above."
    echo "Service is installed. You can still use nmcli to manage connections."
    exit 1
fi

make install
echo "Plasma plugin installed."

echo ""
echo "Done. Pangolin VPN is available in:"
echo "  - KDE System Settings > Network > Connections > Add > VPN"
echo "  - nmcli connection add type vpn vpn-type pangolin con-name 'Pangolin VPN'"
