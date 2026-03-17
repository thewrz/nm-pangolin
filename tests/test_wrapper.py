"""Tests for pangolin_wrapper module."""

import json
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pangolin_wrapper as wrapper
from pangolin_wrapper import PangolinNotFoundError


# --- Fixtures ---

@pytest.fixture
def mock_pwnam():
    """Mock pwd.getpwnam to return a fake user entry."""
    pw = MagicMock()
    pw.pw_dir = "/home/testuser"
    with patch("pangolin_wrapper.pwd.getpwnam", return_value=pw) as m:
        yield m


# --- find_pangolin ---

def test_find_pangolin_via_which():
    with patch("pangolin_wrapper.shutil.which", return_value="/usr/bin/pangolin"), \
         patch("pangolin_wrapper.os.path.realpath", return_value="/usr/bin/pangolin"):
        assert wrapper.find_pangolin() == "/usr/bin/pangolin"


def test_find_pangolin_fallback_paths():
    with patch("pangolin_wrapper.shutil.which", return_value=None), \
         patch("pangolin_wrapper._user_local_paths", return_value=[]), \
         patch("pangolin_wrapper.os.path.isfile", side_effect=lambda p: p == "/usr/bin/pangolin"), \
         patch("pangolin_wrapper.os.access", return_value=True):
        assert wrapper.find_pangolin() == "/usr/bin/pangolin"


def test_find_pangolin_user_local():
    with patch("pangolin_wrapper.shutil.which", return_value=None), \
         patch("pangolin_wrapper._user_local_paths", return_value=["/home/testuser/.local/bin/pangolin"]), \
         patch("pangolin_wrapper.os.path.isfile", side_effect=lambda p: p == "/home/testuser/.local/bin/pangolin"), \
         patch("pangolin_wrapper.os.access", return_value=True):
        assert wrapper.find_pangolin() == "/home/testuser/.local/bin/pangolin"


def test_find_pangolin_not_found():
    with patch("pangolin_wrapper.shutil.which", return_value=None), \
         patch("pangolin_wrapper._user_local_paths", return_value=[]), \
         patch("pangolin_wrapper.os.path.isfile", return_value=False):
        with pytest.raises(PangolinNotFoundError):
            wrapper.find_pangolin()


# --- start ---

def test_start_basic(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.Popen") as mock_popen:
        proc = wrapper.start("/usr/bin/pangolin", "testuser")

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["/usr/bin/pangolin", "up", "--silent", "--no-override-dns"]

        env = mock_popen.call_args[1]["env"]
        assert env["HOME"] == "/home/testuser"
        assert env["XDG_CONFIG_HOME"] == "/home/testuser/.config"
        assert env["USER"] == "testuser"


def test_start_with_all_options(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.Popen") as mock_popen:
        wrapper.start("/usr/bin/pangolin", "testuser", org="myorg", iface="tun0", no_override_dns=True)

        cmd = mock_popen.call_args[0][0]
        assert "--no-override-dns" in cmd
        assert "--org" in cmd
        assert "myorg" in cmd
        assert "--interface-name" in cmd
        assert "tun0" in cmd


def test_start_without_dns_override(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.Popen") as mock_popen:
        wrapper.start("/usr/bin/pangolin", "testuser", no_override_dns=False)

        cmd = mock_popen.call_args[0][0]
        assert "--no-override-dns" not in cmd


# --- stop ---

def test_stop_success(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result) as mock_run:
        wrapper.stop("/usr/bin/pangolin", "testuser")

        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/bin/pangolin", "down"]


def test_stop_nonzero_exit(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = b"some error"
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        # Should not raise, just log warning
        wrapper.stop("/usr/bin/pangolin", "testuser")


def test_stop_timeout(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pangolin down", timeout=10)):
        with pytest.raises(subprocess.TimeoutExpired):
            wrapper.stop("/usr/bin/pangolin", "testuser")


# --- status ---

def test_status_success(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({"status": "connected", "ip": "10.0.0.1"}).encode()
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        result = wrapper.status("/usr/bin/pangolin", "testuser")
        assert result == {"status": "connected", "ip": "10.0.0.1"}


def test_status_timeout(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="status", timeout=5)):
        assert wrapper.status("/usr/bin/pangolin", "testuser") is None


def test_status_bad_json(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = b"not json at all"
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") is None


def test_status_nonzero_exit(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") is None


# --- get_interface_config ---

def test_get_interface_config_success():
    addr_data = [{"addr_info": [{"family": "inet", "local": "10.0.0.5", "prefixlen": 24}]}]
    route_data = []

    addr_result = MagicMock(returncode=0, stdout=json.dumps(addr_data).encode())
    route_result = MagicMock(returncode=0, stdout=json.dumps(route_data).encode())

    with patch("pangolin_wrapper.subprocess.run", side_effect=[addr_result, route_result]):
        cfg = wrapper.get_interface_config("pangolin")
        assert cfg["address"] == "10.0.0.5"
        assert cfg["prefix"] == 24
        assert cfg["gateway"] is None
        assert cfg["dns"] == []


def test_get_interface_config_no_ipv4():
    addr_data = [{"addr_info": [{"family": "inet6", "local": "::1", "prefixlen": 128}]}]
    addr_result = MagicMock(returncode=0, stdout=json.dumps(addr_data).encode())

    with patch("pangolin_wrapper.subprocess.run", return_value=addr_result):
        with pytest.raises(RuntimeError, match="No IPv4 address"):
            wrapper.get_interface_config("pangolin")


def test_get_interface_config_with_gateway():
    addr_data = [{"addr_info": [{"family": "inet", "local": "10.0.0.5", "prefixlen": 24}]}]
    route_data = [{"dst": "default", "gateway": "10.0.0.1"}]

    addr_result = MagicMock(returncode=0, stdout=json.dumps(addr_data).encode())
    route_result = MagicMock(returncode=0, stdout=json.dumps(route_data).encode())

    with patch("pangolin_wrapper.subprocess.run", side_effect=[addr_result, route_result]):
        cfg = wrapper.get_interface_config("pangolin")
        assert cfg["gateway"] == "10.0.0.1"


# --- cleanup_orphans ---

def test_cleanup_orphans_kills_and_removes():
    pgrep_result = MagicMock(returncode=0, stdout=b"1234\n5678\n")
    ip_show_result = MagicMock(returncode=0)
    kill_result = MagicMock()
    ip_delete_result = MagicMock()

    with patch("pangolin_wrapper.subprocess.run", side_effect=[
        pgrep_result, kill_result, kill_result, ip_show_result, ip_delete_result,
    ]) as mock_run:
        wrapper.cleanup_orphans("/usr/bin/pangolin")

        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["pgrep", "-x", "pangolin"]
        assert calls[1][0][0] == ["kill", "-TERM", "1234"]
        assert calls[2][0][0] == ["kill", "-TERM", "5678"]
        assert calls[3][0][0] == ["ip", "link", "show", "pangolin"]
        assert calls[4][0][0] == ["ip", "link", "delete", "pangolin"]


def test_cleanup_orphans_nothing_to_clean():
    pgrep_result = MagicMock(returncode=1, stdout=b"")
    ip_show_result = MagicMock(returncode=1)

    with patch("pangolin_wrapper.subprocess.run", side_effect=[pgrep_result, ip_show_result]):
        wrapper.cleanup_orphans("/usr/bin/pangolin")


# --- _user_env ---

def test_user_env(mock_pwnam):
    env = wrapper._user_env("testuser")
    assert env["HOME"] == "/home/testuser"
    assert env["XDG_CONFIG_HOME"] == "/home/testuser/.config"
    assert env["USER"] == "testuser"
    assert env["LOGNAME"] == "testuser"


def test_user_env_unknown_user():
    with patch("pangolin_wrapper.pwd.getpwnam", side_effect=KeyError("no such user")):
        with pytest.raises(ValueError, match="Unknown system user"):
            wrapper._user_env("nonexistent")


# --- _run_as_user_cmd ---

def test_run_as_user_cmd():
    cmd = wrapper._run_as_user_cmd("alice", "/usr/bin/pangolin", "up", "--silent")
    assert cmd == ["/usr/bin/pangolin", "up", "--silent"]
