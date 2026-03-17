# nm-pangolin

NetworkManager VPN plugin for Pangolin zero-trust VPN.

## Project Overview

This project wraps the Pangolin CLI (`pangolin up/down`) in a NetworkManager VPN service plugin, enabling desktop integration with KDE Plasma's network applet and other NM frontends.

## Architecture

- **Core**: Python D-Bus service implementing `org.freedesktop.NetworkManager.VPN.Plugin`
- **NM talks to us via D-Bus**, we talk to Pangolin CLI via subprocess
- **No direct WireGuard management** — we delegate entirely to `pangolin` binary

## Key Components

```
src/
  nm_pangolin_service.py   # Main D-Bus service daemon
  pangolin_wrapper.py      # Subprocess wrapper for pangolin CLI
  config.py                # Connection property handling
conf/
  nm-pangolin.name         # NM VPN plugin descriptor (/etc/NetworkManager/VPN/)
  nm-pangolin-service.service  # D-Bus system service activation
  nm-pangolin.conf         # D-Bus system policy (allow NM to talk to us)
tests/
  test_wrapper.py          # Unit tests for CLI wrapper
  test_service.py          # D-Bus service tests (mock)
```

## D-Bus Interface

We implement `org.freedesktop.NetworkManager.VPN.Plugin` at path `/org/freedesktop/NetworkManager/VPN/Plugin`:

### Required Methods
- `Connect(a{sv} connection)` — NM calls this to start VPN. We run `pangolin up --attach`.
- `Disconnect()` — NM calls this to stop VPN. We kill the attach-mode process.
- `NeedSecrets(a{sv} connection) -> s` — Return `auth-token` if not authenticated, empty string if auth is done.

### Required Signals
- `StateChanged(u state)` — Emit when state changes (3=started, 4=stopping, 5=stopped, 6=failed)
- `Ip4Config(a{sv} config)` — Emit after connect with IP/DNS info from pangolin interface
- `Failure(u reason)` — Emit on connection failure

## NM VPN States (NMVpnServiceState)
- 1 = Unknown
- 2 = Init
- 3 = Starting / Started (shuttdown?)
- 4 = Stopping
- 5 = Stopped
- 6 = Failed

Wait — these are actually NMVpnConnectionState. The service states are different. Check the NM docs:
- Service: `NM_VPN_SERVICE_STATE_*` (1=unknown, 2=init, 3=shutdown, 4=starting, 5=started, 6=stopping, 7=stopped)
- Connection: `NM_VPN_CONNECTION_STATE_*`

**Always verify against the actual NM D-Bus spec before implementing.**

## Pangolin CLI Reference

```bash
pangolin up [--silent] [--attach] [--interface-name NAME] [--mtu N] [--org ORG]
pangolin down
pangolin status [--json]
```

- Auth state stored in `~/.config/pangolin/accounts.json`
- Creates a `pangolin` TUN interface
- Runs as the invoking user (not root by default)
- `--silent` disables TUI in detached mode (NOT compatible with `--attach`)
- `--attach` runs in foreground mode (process stays alive as tunnel) — this is what the NM service uses

## Development Guidelines

- Python 3.10+ minimum
- Use `dbus-python` for D-Bus (it's what other NM VPN plugins use)
- Tests should mock subprocess calls, not require a running pangolin instance
- Keep the service daemon minimal — it's a thin wrapper, not a reimplementation
- Follow NM VPN plugin conventions (see nm-openvpn, nm-vpnc as references)

## Permissions / Security

- The D-Bus service runs as root (NM launches it via D-Bus activation)
- `pangolin` CLI runs as root with the connecting user's HOME/XDG_CONFIG_HOME env vars so it finds the right auth state
- D-Bus policy file restricts who can call our methods (only root/NM)

## Testing

```bash
pytest tests/
```

For manual integration testing:
```bash
# Install files to system paths (see README)
# Then:
nmcli connection add type vpn vpn-type pangolin con-name "Pangolin VPN"
nmcli connection up "Pangolin VPN"
journalctl -u NetworkManager -f  # Watch for our service logs
```

## Common Gotchas

- **Use `--attach` not `--silent`**: Detached mode (`--silent`) spawns a background daemon that dies silently in the D-Bus service context. `--attach` keeps the process as the tunnel — killing it tears down cleanly.
- NM expects the VPN service to emit `Ip4Config` signal after successful connect, or it considers the connection failed.
- **Ip4Config must include a gateway**: NM rejects VPN connections with gateway=0. Use the pangolin peer endpoint IP as the gateway.
- **Split-tunnel: set `never-default`**: Pangolin routes specific subnets, not all traffic. Without `never-default=true` in Ip4Config (and `ipv4.never-default yes` on the connection), NM makes the VPN the default route, breaking internet.
- **Interface race condition**: `pangolin status --json` reports "connected" before the TUN interface exists. The service polls for the interface after status reports connected.
- `pangolin status --json` returns non-JSON text ("No client is currently running") with exit code 0 when no client is running. Check for JSON before parsing.
- The `.name` file `service` field must exactly match the D-Bus service name.
- NM will kill the service process if it doesn't respond within a timeout (~60s).
- DNS: we pass `--override-dns=false` so pangolin doesn't write resolv.conf. NM manages DNS from the Ip4Config signal.
