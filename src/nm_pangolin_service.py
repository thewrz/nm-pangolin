#!/usr/bin/env python3
"""NetworkManager VPN plugin service for Pangolin.

Implements the org.freedesktop.NetworkManager.VPN.Plugin D-Bus interface,
delegating tunnel management to the pangolin CLI via pangolin_wrapper.
"""

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import logging
import struct
import socket
import signal
import subprocess
import sys
import time

try:
    from . import pangolin_wrapper as wrapper
    from . import config
except ImportError:
    import pangolin_wrapper as wrapper
    import config

log = logging.getLogger(__name__)

BUS_NAME = "org.freedesktop.NetworkManager.pangolin"
OBJECT_PATH = "/org/freedesktop/NetworkManager/VPN/Plugin"
VPN_IFACE = "org.freedesktop.NetworkManager.VPN.Plugin"

# NM VPN Service States (NM_VPN_SERVICE_STATE_*)
STATE_UNKNOWN = 1
STATE_INIT = 2
STATE_SHUTDOWN = 3
STATE_STARTING = 4
STATE_STARTED = 5
STATE_STOPPING = 6
STATE_STOPPED = 7

# VPN Connection failure reasons
FAILURE_CONNECT_FAILED = 1

# Poll configuration
POLL_INTERVAL_MS = 500
CONNECT_TIMEOUT_S = 30
STARTUP_GRACE_S = 1.0
IDLE_TIMEOUT_S = 30


class NMPangolinService(dbus.service.Object):
    """NetworkManager VPN plugin service for Pangolin."""

    def __init__(self, bus: dbus.SystemBus, pangolin_path: str):
        self._bus_name = dbus.service.BusName(BUS_NAME, bus)
        super().__init__(bus, OBJECT_PATH)

        self._pangolin_path = pangolin_path
        self._state = STATE_INIT
        self._process = None
        self._poll_source = None
        self._idle_source = None
        self._cancelling = False
        self._connect_start = 0.0
        self._user = None
        self._iface = "pangolin"
        self._loop = None

        log.info("Service initialized, pangolin at %s", pangolin_path)

    def set_main_loop(self, loop: GLib.MainLoop) -> None:
        """Store a reference to the GLib main loop for shutdown."""
        self._loop = loop

    def _set_state(self, new_state: int) -> None:
        """Transition to new_state and emit StateChanged signal."""
        old = self._state
        log.info("State: %d -> %d", old, new_state)
        self._state = new_state
        self.StateChanged(dbus.UInt32(new_state))

    def _cancel_poll(self) -> None:
        """Remove the GLib poll timeout if active."""
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
            self._poll_source = None

    def _cancel_idle(self) -> None:
        """Remove the idle timeout if active."""
        if self._idle_source is not None:
            GLib.source_remove(self._idle_source)
            self._idle_source = None

    def _schedule_idle_timeout(self) -> None:
        """After disconnect, wait IDLE_TIMEOUT_S then exit if no new Connect."""
        self._cancel_idle()
        self._idle_source = GLib.timeout_add_seconds(
            IDLE_TIMEOUT_S, self._on_idle_timeout
        )

    def _on_idle_timeout(self) -> bool:
        """Exit the main loop after idle period. D-Bus activation restarts us."""
        self._idle_source = None
        if self._state in (STATE_INIT, STATE_STOPPED):
            log.info("Idle timeout reached, shutting down")
            if self._loop is not None:
                self._loop.quit()
        return False

    def _kill_process(self) -> None:
        """Terminate the pangolin subprocess if running."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
        except OSError as exc:
            log.warning("Error killing pangolin process: %s", exc)
        finally:
            self._process = None

    # --- D-Bus Methods ---

    @dbus.service.method(VPN_IFACE, in_signature="a{sa{sv}}", out_signature="")
    def Connect(self, connection):
        """Start VPN connection. Called by NetworkManager."""
        if self._state not in (STATE_INIT, STATE_STOPPED):
            log.warning("Connect called in invalid state %d", self._state)
            self.Failure(dbus.UInt32(FAILURE_CONNECT_FAILED))
            return

        self._cancel_idle()

        try:
            settings = config.parse_connection(dict(connection))
        except config.ConfigError as exc:
            log.error("Invalid connection settings: %s", exc)
            self.Failure(dbus.UInt32(FAILURE_CONNECT_FAILED))
            self._set_state(STATE_STOPPED)
            return

        self._user = settings["user"]
        self._iface = settings["interface_name"]
        self._cancelling = False

        wrapper.cleanup_orphans(self._pangolin_path, self._iface)

        self._set_state(STATE_STARTING)

        try:
            self._process = wrapper.start(
                self._pangolin_path,
                self._user,
                org=settings["org"],
                iface=settings["interface_name"],
            )
        except (OSError, ValueError) as exc:
            log.error("Failed to start pangolin: %s", exc)
            self.Failure(dbus.UInt32(FAILURE_CONNECT_FAILED))
            self._set_state(STATE_STOPPED)
            return

        self._connect_start = time.monotonic()

        GLib.timeout_add(
            int(STARTUP_GRACE_S * 1000),
            self._start_polling,
        )

    @dbus.service.method(VPN_IFACE, in_signature="a{sa{sv}}a{sv}", out_signature="")
    def ConnectInteractive(self, connection, details):
        """Interactive connect -- delegates to Connect."""
        self.Connect(connection)

    @dbus.service.method(VPN_IFACE, in_signature="", out_signature="")
    def Disconnect(self):
        """Stop VPN connection. Called by NetworkManager."""
        self._cancelling = True
        self._cancel_poll()

        self._set_state(STATE_STOPPING)

        self._kill_process()

        if self._user is not None:
            try:
                wrapper.stop(self._pangolin_path, self._user)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("pangolin down failed: %s", exc)

        self._user = None
        self._set_state(STATE_STOPPED)
        self._schedule_idle_timeout()

    @dbus.service.method(VPN_IFACE, in_signature="a{sa{sv}}", out_signature="s")
    def NeedSecrets(self, connection):
        """Return empty string -- pangolin handles its own auth."""
        return ""

    @dbus.service.method(VPN_IFACE, in_signature="a{sa{sv}}", out_signature="")
    def NewSecrets(self, connection):
        """Accept new secrets (no-op for pangolin)."""

    @dbus.service.method(VPN_IFACE, in_signature="a{sv}", out_signature="")
    def SetConfig(self, config_dict):
        """Accept generic config from NM (no-op)."""

    @dbus.service.method(VPN_IFACE, in_signature="a{sv}", out_signature="")
    def SetIp4Config(self, config_dict):
        """Accept IPv4 config from NM (no-op)."""

    @dbus.service.method(VPN_IFACE, in_signature="a{sv}", out_signature="")
    def SetIp6Config(self, config_dict):
        """Accept IPv6 config from NM (no-op)."""

    @dbus.service.method(VPN_IFACE, in_signature="u", out_signature="")
    def SetFailure(self, reason):
        """Accept failure notification from NM (no-op)."""

    # --- D-Bus Signals ---

    @dbus.service.signal(VPN_IFACE, signature="u")
    def StateChanged(self, state):
        """Emitted when the VPN service state changes."""

    @dbus.service.signal(VPN_IFACE, signature="a{sv}")
    def Ip4Config(self, config_dict):
        """Emitted with IPv4 configuration after successful connect."""

    @dbus.service.signal(VPN_IFACE, signature="u")
    def Failure(self, reason):
        """Emitted when the VPN connection fails."""

    # --- Polling ---

    def _start_polling(self) -> bool:
        """Begin status polling (called once after grace period)."""
        if self._cancelling:
            return False

        self._poll_source = GLib.timeout_add(POLL_INTERVAL_MS, self._poll_status)
        return False

    def _poll_status(self) -> bool:
        """Poll pangolin status. Returns True to continue, False to stop."""
        if self._cancelling:
            self._poll_source = None
            return False

        if self._check_process_exited():
            return False

        if self._check_connect_timeout():
            return False

        return self._check_pangolin_status()

    def _check_process_exited(self) -> bool:
        """Check if the pangolin process exited unexpectedly. Returns True if exited."""
        if self._process is None or self._process.poll() is None:
            return False

        rc = self._process.returncode
        log.error("pangolin exited with code %d", rc)
        self._process = None
        self._poll_source = None
        self.Failure(dbus.UInt32(FAILURE_CONNECT_FAILED))
        self._set_state(STATE_STOPPED)
        self._schedule_idle_timeout()
        return True

    def _check_connect_timeout(self) -> bool:
        """Check if the connection attempt has timed out. Returns True if timed out."""
        elapsed = time.monotonic() - self._connect_start
        if elapsed <= CONNECT_TIMEOUT_S:
            return False

        log.error("Connection timed out after %.1fs", elapsed)
        self._kill_process()
        self._poll_source = None
        self.Failure(dbus.UInt32(FAILURE_CONNECT_FAILED))
        self._set_state(STATE_STOPPED)
        self._schedule_idle_timeout()
        return True

    def _check_pangolin_status(self) -> bool:
        """Query pangolin status and handle connected state. Returns True to keep polling."""
        st = wrapper.status(self._pangolin_path, self._user, timeout=2)
        if st is None or self._cancelling:
            return not self._cancelling

        connected = st.get("connected", False) or st.get("status") == "connected"
        if not connected:
            return True

        log.info("Pangolin connected")
        self._poll_source = None

        try:
            iface_config = wrapper.get_interface_config(self._iface)
            ip4 = self._build_ip4_config(iface_config)
        except RuntimeError as exc:
            log.error("Failed to read interface config: %s", exc)
            self._kill_process()
            self.Failure(dbus.UInt32(FAILURE_CONNECT_FAILED))
            self._set_state(STATE_STOPPED)
            self._schedule_idle_timeout()
            return False

        ip4 = _merge_dns_from_status(ip4, st)

        self.Ip4Config(ip4)
        self._set_state(STATE_STARTED)
        return False

    def _build_ip4_config(self, iface_config: dict) -> dict:
        """Build NM Ip4Config D-Bus dict from parsed interface config."""
        addr = iface_config["address"]
        prefix = iface_config["prefix"]
        gateway = iface_config.get("gateway")

        addr_packed = _pack_ipv4(addr)
        gw_packed = _pack_ipv4(gateway) if gateway else dbus.UInt32(0)

        ip4 = {
            "tundev": dbus.String(self._iface),
            "gateway": gw_packed,
            "addresses": dbus.Array(
                [dbus.Struct(
                    (addr_packed, dbus.UInt32(prefix), gw_packed),
                    signature="uuu",
                )],
                signature="(uuu)",
            ),
        }

        dns_list = iface_config.get("dns", [])
        if dns_list:
            ip4["dns"] = dbus.Array(
                [dbus.UInt32(_pack_ipv4(d)) for d in dns_list],
                signature="u",
            )

        return ip4


def _merge_dns_from_status(ip4: dict, status_data: dict) -> dict:
    """Merge DNS servers from pangolin status into ip4 config if not already present."""
    if ip4.get("dns"):
        return ip4

    dns_servers = status_data.get("dns", [])
    dns_packed = dbus.Array(
        [dbus.UInt32(_pack_ipv4(d)) for d in dns_servers if _is_valid_ipv4(d)],
        signature="u",
    )

    if not dns_packed:
        return ip4

    return {**ip4, "dns": dns_packed}


def _pack_ipv4(addr: str) -> dbus.UInt32:
    """Pack an IPv4 address string into a host-byte-order UInt32 (NM convention)."""
    return dbus.UInt32(struct.unpack("=I", socket.inet_pton(socket.AF_INET, addr))[0])


def _is_valid_ipv4(addr: str) -> bool:
    """Check if a string is a valid IPv4 address (strict dotted-quad only)."""
    try:
        socket.inet_pton(socket.AF_INET, addr)
        return True
    except OSError:
        return False


def main():
    """Entry point for the NM Pangolin VPN service."""
    if not logging.root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="nm-pangolin: %(levelname)s: %(message)s",
        )

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    try:
        pangolin_path = wrapper.find_pangolin()
    except wrapper.PangolinNotFoundError:
        log.critical("pangolin binary not found -- cannot start service")
        sys.exit(1)

    bus = dbus.SystemBus()
    service = NMPangolinService(bus, pangolin_path)

    wrapper.cleanup_orphans(pangolin_path)

    loop = GLib.MainLoop()
    service.set_main_loop(loop)

    def handle_signal(signum, frame):
        log.info("Received signal %d, shutting down", signum)
        loop.quit()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("NM Pangolin VPN service started")
    service._set_state(STATE_STOPPED)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Service exiting")
