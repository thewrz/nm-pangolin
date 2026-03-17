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
- `Connect(a{sv} connection)` — NM calls this to start VPN. We run `pangolin up --silent`.
- `Disconnect()` — NM calls this to stop VPN. We run `pangolin down`.
- `NeedSecrets(a{sv} connection) -> s` — Return empty string (pangolin handles its own auth).

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
- `--silent` disables TUI, required for daemon use

## Development Guidelines

- Python 3.10+ minimum
- Use `dbus-python` for D-Bus (it's what other NM VPN plugins use)
- Tests should mock subprocess calls, not require a running pangolin instance
- Keep the service daemon minimal — it's a thin wrapper, not a reimplementation
- Follow NM VPN plugin conventions (see nm-openvpn, nm-vpnc as references)

## Permissions / Security

- The D-Bus service runs as root (NM launches it)
- `pangolin` CLI runs as the connecting user — use `su`/`runuser` from the service
- D-Bus policy file must restrict who can call our methods (only NM)

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

- NM expects the VPN service to emit `Ip4Config` signal after successful connect, or it considers the connection failed
- The `.name` file `service` field must exactly match the D-Bus service name
- NM will kill the service process if it doesn't respond within a timeout
- `pangolin up` in detached mode returns immediately but the tunnel isn't ready — poll `pangolin status --json` for connected state
- DNS: pangolin uses `--override-dns` by default, which writes resolv conf. NM also manages DNS. These will fight. Need to either disable pangolin's DNS override and let NM handle it, or pass pangolin's DNS config back to NM via the Ip4Config signal.
