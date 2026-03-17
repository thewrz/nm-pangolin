"""Tests for nm_pangolin_service module.

Mocks dbus, GLib, and all subprocess-dependent modules so tests run
without system D-Bus or a running pangolin instance.
"""

import struct
import socket
import subprocess
import sys
import os
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# --- Mock dbus and GLib before importing the service module ---

mock_dbus = MagicMock()
mock_dbus.UInt32 = lambda x: x
mock_dbus.String = lambda x: x
mock_dbus.Array = lambda items, signature="": list(items)
mock_dbus.Struct = lambda items, signature="": tuple(items)
mock_dbus.service.Object = object
mock_dbus.service.BusName = MagicMock()
mock_dbus.service.method = lambda *a, **kw: lambda f: f
mock_dbus.service.signal = lambda *a, **kw: lambda f: f
mock_dbus.SystemBus = MagicMock

mock_dbus_mainloop = MagicMock()
mock_dbus.mainloop = MagicMock()
mock_dbus.mainloop.glib = mock_dbus_mainloop

sys.modules["dbus"] = mock_dbus
sys.modules["dbus.service"] = mock_dbus.service
sys.modules["dbus.mainloop"] = mock_dbus.mainloop
sys.modules["dbus.mainloop.glib"] = mock_dbus_mainloop

mock_gi = MagicMock()
mock_glib = MagicMock()
mock_gi.repository.GLib = mock_glib
sys.modules["gi"] = mock_gi
sys.modules["gi.repository"] = mock_gi.repository

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import nm_pangolin_service as service
from nm_pangolin_service import (
    NMPangolinService,
    _pack_ipv4,
    _is_valid_ipv4,
    _merge_dns_from_status,
    STATE_INIT,
    STATE_STARTING,
    STATE_STARTED,
    STATE_STOPPING,
    STATE_STOPPED,
    FAILURE_CONNECT_FAILED,
)


# --- Fixtures ---

@pytest.fixture
def svc():
    """Create a service instance with mocked bus."""
    mock_glib.reset_mock()
    mock_glib.timeout_add = MagicMock(return_value=42)
    mock_glib.timeout_add_seconds = MagicMock(return_value=99)
    mock_glib.source_remove = MagicMock()

    s = NMPangolinService.__new__(NMPangolinService)
    s._bus_name = MagicMock()
    s._pangolin_path = "/usr/bin/pangolin"
    s._state = STATE_INIT
    s._process = None
    s._poll_source = None
    s._idle_source = None
    s._cancelling = False
    s._connect_start = 0.0
    s._user = None
    s._iface = "pangolin"
    s._loop = MagicMock()

    s.StateChanged = MagicMock()
    s.Ip4Config = MagicMock()
    s.Failure = MagicMock()
    return s


def _valid_connection():
    return {
        "vpn": {"data": {"server-url": "https://vpn.example.com"}},
        "connection": {"permissions": ["user:testuser:"]},
    }


@pytest.fixture
def mock_config():
    with patch.object(service, "config") as m:
        m.ConfigError = type("ConfigError", (Exception,), {})
        m.parse_connection.return_value = {
            "server_url": "https://vpn.example.com",
            "org": None,
            "interface_name": "pangolin",
            "mtu": None,
            "olm_id": None,
            "olm_secret": None,
            "user": "testuser",
        }
        yield m


@pytest.fixture
def mock_wrapper():
    with patch.object(service, "wrapper") as m:
        m.cleanup_orphans = MagicMock()
        m.start = MagicMock(return_value=MagicMock())
        m.stop = MagicMock()
        m.status = MagicMock(return_value=None)
        m.get_interface_config = MagicMock(return_value={
            "address": "10.0.0.5",
            "prefix": 24,
            "gateway": "10.0.0.1",
            "dns": [],
        })
        yield m


# --- Connect lifecycle ---

def test_connect_success_lifecycle(svc, mock_config, mock_wrapper):
    svc._state = STATE_STOPPED
    svc.Connect(_valid_connection())

    svc.StateChanged.assert_any_call(STATE_STARTING)
    mock_wrapper.cleanup_orphans.assert_called_once()
    mock_wrapper.start.assert_called_once()
    mock_glib.timeout_add.assert_called_once()


def test_connect_invalid_settings(svc, mock_wrapper):
    with patch.object(service, "config") as mc:
        mc.ConfigError = type("ConfigError", (Exception,), {})
        mc.parse_connection.side_effect = mc.ConfigError("bad")
        svc._state = STATE_STOPPED
        svc.Connect(_valid_connection())

    svc.Failure.assert_called_with(FAILURE_CONNECT_FAILED)
    svc.StateChanged.assert_any_call(STATE_STOPPED)


def test_connect_start_fails(svc, mock_config, mock_wrapper):
    mock_wrapper.start.side_effect = OSError("exec failed")
    svc._state = STATE_STOPPED
    svc.Connect(_valid_connection())

    svc.Failure.assert_called_with(FAILURE_CONNECT_FAILED)
    svc.StateChanged.assert_any_call(STATE_STOPPED)


def test_connect_rejected_when_starting(svc, mock_config, mock_wrapper):
    svc._state = STATE_STARTING
    svc.Connect(_valid_connection())

    svc.Failure.assert_called_with(FAILURE_CONNECT_FAILED)
    mock_wrapper.start.assert_not_called()


# --- Disconnect ---

def test_disconnect_during_connect(svc, mock_config, mock_wrapper):
    svc._state = STATE_STOPPED
    svc.Connect(_valid_connection())

    svc._state = STATE_STARTING
    svc._poll_source = 42
    svc.Disconnect()

    assert svc._cancelling is True
    mock_glib.source_remove.assert_called()
    svc.StateChanged.assert_any_call(STATE_STOPPING)
    svc.StateChanged.assert_any_call(STATE_STOPPED)


def test_disconnect_lifecycle(svc, mock_wrapper):
    svc._state = STATE_STARTED
    svc._user = "testuser"
    svc._process = MagicMock()
    svc._process.terminate = MagicMock()
    svc._process.wait = MagicMock()

    svc.Disconnect()

    svc.StateChanged.assert_any_call(STATE_STOPPING)
    svc.StateChanged.assert_any_call(STATE_STOPPED)
    mock_wrapper.stop.assert_called_once()


# --- Polling ---

def test_poll_process_exited(svc, mock_wrapper):
    proc = MagicMock()
    proc.poll.return_value = 1
    proc.returncode = 1
    svc._process = proc
    svc._state = STATE_STARTING
    svc._connect_start = time.monotonic()

    result = svc._poll_status()

    assert result is False
    svc.Failure.assert_called_with(FAILURE_CONNECT_FAILED)
    svc.StateChanged.assert_any_call(STATE_STOPPED)


def test_poll_timeout(svc, mock_wrapper):
    svc._process = MagicMock()
    svc._process.poll.return_value = None
    svc._state = STATE_STARTING
    svc._connect_start = time.monotonic() - 60
    svc._user = "testuser"

    result = svc._poll_status()

    assert result is False
    svc.Failure.assert_called_with(FAILURE_CONNECT_FAILED)


def test_poll_not_connected_yet(svc, mock_wrapper):
    svc._process = MagicMock()
    svc._process.poll.return_value = None
    svc._state = STATE_STARTING
    svc._connect_start = time.monotonic()
    svc._user = "testuser"
    mock_wrapper.status.return_value = {"status": "connecting"}

    result = svc._poll_status()

    assert result is True


def test_poll_cancelling(svc, mock_wrapper):
    svc._cancelling = True
    result = svc._poll_status()
    assert result is False


# --- NeedSecrets ---

def test_need_secrets_returns_empty(svc):
    assert svc.NeedSecrets({}) == ""


# --- ConnectInteractive ---

def test_connect_interactive_delegates(svc, mock_config, mock_wrapper):
    svc._state = STATE_STOPPED
    with patch.object(svc, "Connect") as mock_connect:
        svc.ConnectInteractive(_valid_connection(), {})
        mock_connect.assert_called_once_with(_valid_connection())


# --- Idle timeout ---

def test_idle_timeout(svc):
    svc._state = STATE_STOPPED
    result = svc._on_idle_timeout()

    assert result is False
    svc._loop.quit.assert_called_once()


def test_idle_timeout_cancelled_by_connect(svc, mock_config, mock_wrapper):
    svc._state = STATE_STOPPED
    svc._idle_source = 99

    svc.Connect(_valid_connection())

    mock_glib.source_remove.assert_any_call(99)


# --- Helper functions ---

def test_pack_ipv4():
    packed = _pack_ipv4("10.0.0.1")
    expected = struct.unpack("=I", socket.inet_pton(socket.AF_INET, "10.0.0.1"))[0]
    assert packed == expected


def test_is_valid_ipv4():
    assert _is_valid_ipv4("10.0.0.1") is True
    assert _is_valid_ipv4("192.168.1.1") is True
    assert _is_valid_ipv4("not-an-ip") is False
    assert _is_valid_ipv4("") is False
    assert _is_valid_ipv4("999.999.999.999") is False
    assert _is_valid_ipv4("10") is False  # truncated forms rejected
    assert _is_valid_ipv4("10.1") is False


def test_merge_dns_from_status():
    ip4 = {"tundev": "pangolin", "gateway": 0}
    status_data = {"dns": ["8.8.8.8", "8.8.4.4"]}

    result = _merge_dns_from_status(ip4, status_data)

    expected_dns = [
        struct.unpack("=I", socket.inet_pton(socket.AF_INET, "8.8.8.8"))[0],
        struct.unpack("=I", socket.inet_pton(socket.AF_INET, "8.8.4.4"))[0],
    ]
    assert result["dns"] == expected_dns
    assert result["tundev"] == "pangolin"


def test_merge_dns_from_status_skips_when_present():
    ip4 = {"tundev": "pangolin", "dns": [12345]}
    status_data = {"dns": ["8.8.8.8"]}

    result = _merge_dns_from_status(ip4, status_data)

    assert result["dns"] == [12345]


# --- _build_ip4_config ---

def test_build_ip4_config(svc):
    iface_config = {
        "address": "10.0.0.5",
        "prefix": 24,
        "gateway": "10.0.0.1",
        "dns": [],
    }
    svc._iface = "pangolin"

    result = svc._build_ip4_config(iface_config)

    assert result["tundev"] == "pangolin"
    expected_gw = struct.unpack("=I", socket.inet_pton(socket.AF_INET, "10.0.0.1"))[0]
    assert result["gateway"] == expected_gw
    assert len(result["addresses"]) == 1


# --- Poll connected happy path ---

def test_poll_connected_emits_ip4config(svc, mock_wrapper):
    svc._process = MagicMock()
    svc._process.poll.return_value = None
    svc._state = STATE_STARTING
    svc._connect_start = time.monotonic()
    svc._user = "testuser"
    mock_wrapper.status.return_value = {"status": "connected"}

    result = svc._poll_status()

    assert result is False
    svc.Ip4Config.assert_called_once()
    svc.StateChanged.assert_any_call(STATE_STARTED)


# --- Orphan cleanup on Connect ---

def test_orphan_cleanup_on_startup(svc, mock_config, mock_wrapper):
    svc._state = STATE_STOPPED
    svc.Connect(_valid_connection())

    mock_wrapper.cleanup_orphans.assert_called_once_with("/usr/bin/pangolin", "pangolin")
