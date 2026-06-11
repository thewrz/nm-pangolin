# nm-pangolin

> Follows the global `~/.claude/rules` (code · workflow · security · agents). This file holds only nm-pangolin-specific facts and overrides.

NetworkManager VPN plugin for the Pangolin zero-trust VPN. A Python D-Bus service implementing `org.freedesktop.NetworkManager.VPN.Plugin` that wraps the `pangolin` CLI (`pangolin up/down`) via subprocess — no direct WireGuard management, we delegate entirely to the `pangolin` binary. This gives desktop integration with KDE Plasma's network applet and other NM frontends.

## Layout

```
src/
  nm_pangolin_service.py        # Main D-Bus service daemon
  pangolin_wrapper.py           # Subprocess wrapper for pangolin CLI
  config.py                     # Connection property handling
conf/
  nm-pangolin.name              # NM VPN plugin descriptor (/etc/NetworkManager/VPN/)
  nm-pangolin-service.service   # D-Bus system service activation
  nm-pangolin.conf              # D-Bus system policy (allow NM to talk to us)
tests/
  test_wrapper.py               # Unit tests for CLI wrapper
  test_service.py               # D-Bus service tests (mock)
```

## D-Bus interface

We implement `org.freedesktop.NetworkManager.VPN.Plugin` at path `/org/freedesktop/NetworkManager/VPN/Plugin`.

Methods:
- `Connect(a{sv} connection)` — NM calls this to start VPN. We run `pangolin up --attach`.
- `Disconnect()` — NM calls this to stop VPN. We kill the attach-mode process.
- `NeedSecrets(a{sv} connection) -> s` — Return `auth-token` if not authenticated, empty string if auth is done.

Signals:
- `StateChanged(u state)` — Emit on state change.
- `Ip4Config(a{sv} config)` — Emit after connect with IP/DNS info from the pangolin interface.
- `Failure(u reason)` — Emit on connection failure.

NM uses two distinct state enums — do not conflate them. Service: `NM_VPN_SERVICE_STATE_*` (1=unknown, 2=init, 3=shutdown, 4=starting, 5=started, 6=stopping, 7=stopped). Connection: `NM_VPN_CONNECTION_STATE_*`. **Always verify the exact values against the live NM D-Bus spec before implementing.** Reference implementations: nm-openvpn, nm-vpnc.

## Pangolin CLI reference

```bash
pangolin up [--silent] [--attach] [--interface-name NAME] [--mtu N] [--org ORG]
pangolin down
pangolin status [--json]
```

- Auth state stored in `~/.config/pangolin/accounts.json`.
- Creates a `pangolin` TUN interface.
- `--attach` runs in foreground (process stays alive as the tunnel) — this is what the NM service uses.
- `--silent` disables the TUI in detached mode and is NOT compatible with `--attach`.

## Process / permissions model

- The D-Bus service runs as **root** (NM launches it via D-Bus activation); the D-Bus policy file restricts callers to root/NM.
- The `pangolin` CLI is invoked as root but with the connecting user's `HOME`/`XDG_CONFIG_HOME` env vars so it finds the right auth state in `~/.config/pangolin/accounts.json`.

## Build / run / test

```bash
pip install -e ".[dev]"     # Python 3.10+, dbus-python, PyGObject
pytest tests/               # tests mock subprocess — no pangolin binary required

# Manual integration test (after installing files to system paths, see README):
nmcli connection add type vpn vpn-type pangolin con-name "Pangolin VPN"
nmcli connection up "Pangolin VPN"
journalctl -u NetworkManager -f   # watch for service logs
```

## Gotchas

- **Use `--attach`, not `--silent`.** Detached mode spawns a background daemon that dies silently in the D-Bus service context; `--attach` keeps the process as the tunnel so killing it tears down cleanly.
- **Emit `Ip4Config` after connect or NM fails the connection.** NM requires the signal to consider the connection up.
- **`Ip4Config` must include a gateway.** NM rejects VPN connections with gateway=0 — use the pangolin peer endpoint IP as the gateway.
- **Split-tunnel needs `never-default`.** Pangolin routes specific subnets, not all traffic. Without `never-default=true` in `Ip4Config` (and `ipv4.never-default yes` on the connection), NM makes the VPN the default route and breaks internet.
- **Interface race condition.** `pangolin status --json` reports "connected" before the TUN interface exists; the service polls for the interface after status reports connected.
- **`pangolin status --json` is not always JSON.** It returns plain text ("No client is currently running") with exit code 0 when no client is running — check for JSON before parsing.
- The `.name` file `service` field must exactly match the D-Bus service name.
- NM kills the service process if it doesn't respond within a timeout (~60s).
- **DNS:** we pass `--override-dns=false` so pangolin does not write resolv.conf; NM manages DNS from the `Ip4Config` signal.
