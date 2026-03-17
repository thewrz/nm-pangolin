# nm-pangolin

NetworkManager VPN plugin for [Pangolin](https://pangolin.net/) — a self-hosted, identity-aware zero-trust access platform built on WireGuard.

This plugin integrates Pangolin's VPN client with NetworkManager, making it appear as a toggleable VPN connection in KDE Plasma's network applet, GNOME's network menu, and any other NetworkManager frontend.

## Status

**Early development** — not yet functional.

## Why?

Pangolin provides a CLI client (`pangolin up/down`) for Linux but no desktop integration. This project wraps that CLI in a proper NetworkManager VPN service plugin so you can:

- Toggle Pangolin VPN from the KDE/GNOME network dropdown
- See connection status in the system tray
- Auto-connect on login via NetworkManager's built-in autoconnect
- Manage Pangolin alongside other VPN connections in a standard way

## Architecture

```
┌──────────────────────┐     ┌─────────────────────────┐
│   KDE plasma-nm /    │     │   NetworkManager        │
│   GNOME nm-applet    │     │                         │
│   (user clicks       │────▶│   Activates "pangolin"  │
│    "Pangolin VPN")   │     │   VPN connection        │
└──────────────────────┘     └────────┬────────────────┘
                                      │ D-Bus launch
                                      ▼
                             ┌─────────────────────────┐
                             │  nm-pangolin-service     │
                             │  (Python D-Bus daemon)   │
                             │                          │
                             │  Implements NM VPN       │
                             │  Plugin interface        │
                             │  Calls pangolin CLI      │
                             └────────┬────────────────┘
                                      │ subprocess
                                      ▼
                             ┌─────────────────────────┐
                             │  pangolin up / down      │
                             │  (existing CLI client)   │
                             └─────────────────────────┘
```

### Components

| Component | Type | Description |
|-----------|------|-------------|
| `nm-pangolin-service` | Python D-Bus service | Implements `org.freedesktop.NetworkManager.VPN.Plugin` — the core daemon NM launches to manage the connection |
| `nm-pangolin.name` | NM plugin descriptor | INI file telling NM this VPN plugin exists |
| `nm-pangolin-service.service` | D-Bus service file | Tells D-Bus how to activate the daemon on demand |
| Auth dialog (future) | Python/Qt | Prompts for credentials if session expired |
| plasma-nm editor (future) | C++/Qt | GUI settings in KDE's "Add VPN" dialog |

## Requirements

- Python 3.10+
- `dbus-python` (or `dasbus`)
- NetworkManager
- Pangolin CLI (`pangolin`) installed and authenticated
- PyGObject (`gi`) for NM bindings

## Installation

> Not yet packaged. Instructions will be added once the MVP is functional.

```bash
# Manual install (development)
sudo cp conf/nm-pangolin.name /etc/NetworkManager/VPN/
sudo cp conf/nm-pangolin-service.service /usr/share/dbus-1/system-services/
sudo cp src/nm_pangolin_service.py /usr/lib/nm-pangolin/
sudo chmod +x /usr/lib/nm-pangolin/nm_pangolin_service.py

# Create a connection
nmcli connection add type vpn vpn-type pangolin con-name "Pangolin VPN"
```

## Usage

Once installed, "Pangolin VPN" appears in your network applet. Click to connect, click again to disconnect.

CLI equivalent:
```bash
nmcli connection up "Pangolin VPN"
nmcli connection down "Pangolin VPN"
```

## Development

```bash
git clone https://github.com/thewrz/nm-pangolin.git
cd nm-pangolin
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Roadmap

- [ ] MVP: Python D-Bus service wrapping `pangolin up/down`
- [ ] NM plugin descriptor + D-Bus activation
- [ ] Connection status reporting (connected/disconnected/failed)
- [ ] IP/DNS config passthrough to NM
- [ ] Auth dialog for expired sessions
- [ ] `nmcli` property support (org, endpoint, flags)
- [ ] KDE plasma-nm editor plugin (C++/Qt)
- [ ] GNOME nm-connection-editor plugin (GTK)
- [ ] AUR / deb / rpm packaging
- [ ] Upstream contribution to fosrl

## References

- [NM VPN Plugin D-Bus interface](https://networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.VPN.Plugin.html)
- [NM VPN service plugin spec](https://networkmanager.dev/docs/api/latest/nm-vpn-dbus-types.html)
- [plasma-nm VPN plugins](https://github.com/KDE/plasma-nm)
- [GlobalProtect NM plugin (reference impl)](https://github.com/WMP/GlobalProtect-SAML-NetworkManager)
- [NetworkManager-ssh (reference impl)](https://github.com/danfruehauf/NetworkManager-ssh)
- [Pangolin CLI source](https://github.com/fosrl/cli)
- [Pangolin docs](https://docs.pangolin.net/)

## License

MIT
