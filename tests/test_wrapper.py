"""Tests for pangolin_wrapper module."""

import json
import stat
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
# The service runs as root, so it only launches a binary that nothing but root
# can replace. A fake stat table stands in for the filesystem.

ROOT_DIR = (0, stat.S_IFDIR | 0o755)
ROOT_BIN = (0, stat.S_IFREG | 0o755)


def _fake_fs(entries, links=None):
    """Patch stat/realpath/access/which-independent lookups from a table of
    path -> (uid, mode). Unlisted paths do not exist."""
    links = links or {}

    def fake_stat(path, *args, **kwargs):
        path = links.get(os.fspath(path), os.fspath(path))
        if path not in entries:
            raise FileNotFoundError(2, "No such file or directory", path)
        uid, mode = entries[path]
        return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))

    return (
        patch("pangolin_wrapper.os.stat", side_effect=fake_stat),
        patch("pangolin_wrapper.os.path.realpath", side_effect=lambda p: links.get(p, p)),
        patch("pangolin_wrapper.os.access", return_value=True),
    )


def _find(entries, which, links=None):
    stat_p, real_p, access_p = _fake_fs(entries, links)
    with stat_p, real_p, access_p, \
         patch("pangolin_wrapper.shutil.which", return_value=which):
        return wrapper.find_pangolin()


SYSTEM = {"/": ROOT_DIR, "/usr": ROOT_DIR, "/usr/bin": ROOT_DIR,
          "/usr/local": ROOT_DIR, "/usr/local/bin": ROOT_DIR}


def test_find_pangolin_via_which():
    fs = {**SYSTEM, "/usr/bin/pangolin": ROOT_BIN}
    assert _find(fs, which="/usr/bin/pangolin") == "/usr/bin/pangolin"


def test_find_pangolin_fallback_paths():
    fs = {**SYSTEM, "/usr/local/bin/pangolin": ROOT_BIN}
    assert _find(fs, which=None) == "/usr/local/bin/pangolin"


def test_find_pangolin_rejects_symlink_into_home():
    """The old install symlinked /usr/local/bin/pangolin at the user's copy:
    anything running as that user could then run code as root."""
    fs = {**SYSTEM, "/home": ROOT_DIR, "/home/u": (1000, stat.S_IFDIR | 0o700),
          "/home/u/.local": (1000, stat.S_IFDIR | 0o755),
          "/home/u/.local/bin": (1000, stat.S_IFDIR | 0o755),
          "/home/u/.local/bin/pangolin": (1000, stat.S_IFREG | 0o755)}
    links = {"/usr/local/bin/pangolin": "/home/u/.local/bin/pangolin"}
    with pytest.raises(PangolinNotFoundError, match="owned by uid 1000, not root"):
        _find(fs, which="/usr/local/bin/pangolin", links=links)


def test_find_pangolin_rejects_user_writable_directory():
    """A root-owned file is still replaceable if its directory is not root's."""
    fs = {**SYSTEM, "/opt": ROOT_DIR, "/opt/tools": (1000, stat.S_IFDIR | 0o755),
          "/opt/tools/pangolin": ROOT_BIN}
    with pytest.raises(PangolinNotFoundError, match="/opt/tools is owned by uid 1000"):
        _find(fs, which="/opt/tools/pangolin")


def test_find_pangolin_rejects_world_writable():
    fs = {**SYSTEM, "/usr/bin/pangolin": (0, stat.S_IFREG | 0o757)}
    with pytest.raises(PangolinNotFoundError, match="world-writable"):
        _find(fs, which="/usr/bin/pangolin")


def test_find_pangolin_accepts_admin_group_writable_directory():
    """Debian ships /usr/local as root:staff 2775; that is the distribution's
    own trust decision, not a user-writable path."""
    fs = {**SYSTEM, "/usr/local/bin": (0, stat.S_IFDIR | 0o2775),
          "/usr/local/bin/pangolin": ROOT_BIN}
    assert _find(fs, which="/usr/local/bin/pangolin") == "/usr/local/bin/pangolin"


def test_find_pangolin_skips_untrusted_for_a_trusted_one():
    fs = {**SYSTEM, "/home": ROOT_DIR, "/home/u": (1000, stat.S_IFDIR | 0o755),
          "/home/u/pangolin": (1000, stat.S_IFREG | 0o755),
          "/usr/bin/pangolin": ROOT_BIN}
    assert _find(fs, which="/home/u/pangolin") == "/usr/bin/pangolin"


def test_find_pangolin_not_found():
    with pytest.raises(PangolinNotFoundError, match="not found"):
        _find(dict(SYSTEM), which=None)


# --- start ---

def test_start_basic(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.Popen") as mock_popen:
        mock_popen.return_value.stdout = None
        mock_popen.return_value.stderr = None
        proc = wrapper.start("/usr/bin/pangolin", "testuser")

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["/usr/bin/pangolin", "up", "--attach", "--override-dns=false"]

        env = mock_popen.call_args[1]["env"]
        assert env["HOME"] == "/home/testuser"
        assert env["XDG_CONFIG_HOME"] == "/home/testuser/.config"
        assert env["USER"] == "testuser"


def test_start_with_all_options(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.Popen") as mock_popen:
        mock_popen.return_value.stdout = None
        mock_popen.return_value.stderr = None
        wrapper.start("/usr/bin/pangolin", "testuser", org="myorg", iface="tun0", no_override_dns=True)

        cmd = mock_popen.call_args[0][0]
        assert "--override-dns=false" in cmd
        assert "--org" in cmd
        assert "myorg" in cmd
        assert "--interface-name" in cmd
        assert "tun0" in cmd


def test_start_without_dns_override(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.Popen") as mock_popen:
        mock_popen.return_value.stdout = None
        mock_popen.return_value.stderr = None
        wrapper.start("/usr/bin/pangolin", "testuser", no_override_dns=False)

        cmd = mock_popen.call_args[0][0]
        assert "--override-dns=false" not in cmd
        assert "--attach" in cmd


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


def test_status_not_running(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = b"No client is currently running\n"
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") is None


def test_status_empty_stdout(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = b""
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") is None


def test_status_bad_json(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = b"{bad json"
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


# --- status: update-banner tolerance (CLI prints a banner on stdout) ---

def test_status_json_after_update_banner(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = (
        b"A new version is available: 0.16.0 (current: 0.6.1)\n"
        b"Run 'pangolin update' to update to the latest version\n\n"
        + json.dumps({"status": "connected"}).encode()
    )
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") == {"status": "connected"}


def test_status_banner_without_json(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = (
        b"A new version is available: 0.16.0 (current: 0.6.1)\n"
        b"No client is currently running\n"
    )
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") is None


# --- is_authenticated: exit code 0 does NOT mean authenticated ---

def _auth_result(rc, stdout=b"", stderr=b""):
    result = MagicMock()
    result.returncode = rc
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_is_authenticated_ok(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run",
               return_value=_auth_result(0, b"Logged in as user@example.com\n")):
        assert wrapper.is_authenticated("/usr/bin/pangolin", "testuser") is True


def test_is_authenticated_unauthorized_with_rc0(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run",
               return_value=_auth_result(0, b"Failed to fetch user data: Unauthorized\n")):
        assert wrapper.is_authenticated("/usr/bin/pangolin", "testuser") is False


def test_is_authenticated_nonzero_exit(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run", return_value=_auth_result(1)):
        assert wrapper.is_authenticated("/usr/bin/pangolin", "testuser") is False


# --- auth_state tri-state ---

def test_auth_state_yes(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run",
               return_value=_auth_result(0, b"Logged in as user@example.com\n")):
        assert wrapper.auth_state("/usr/bin/pangolin", "testuser") == wrapper.AUTH_YES


def test_auth_state_no_on_marker_even_with_rc0(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run",
               return_value=_auth_result(0, b"Failed to fetch user data: Unauthorized\n")):
        assert wrapper.auth_state("/usr/bin/pangolin", "testuser") == wrapper.AUTH_NO


def test_auth_state_unknown_on_timeout(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="auth", timeout=5)):
        assert wrapper.auth_state("/usr/bin/pangolin", "testuser") == wrapper.AUTH_UNKNOWN


def test_auth_state_unknown_on_unexpected_exit(mock_pwnam):
    with patch("pangolin_wrapper.subprocess.run", return_value=_auth_result(2)):
        assert wrapper.auth_state("/usr/bin/pangolin", "testuser") == wrapper.AUTH_UNKNOWN


# --- status: raw_decode robustness ---

def test_status_json_with_braces_in_banner_and_trailing_text(mock_pwnam):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = (
        b"note {beta} build available\n"
        + json.dumps({"status": "connected"}).encode()
        + b"\ntrailing diagnostics line\n"
    )
    with patch("pangolin_wrapper.subprocess.run", return_value=mock_result):
        assert wrapper.status("/usr/bin/pangolin", "testuser") == {"status": "connected"}


# --- drain threads on a real process ---

def test_start_drains_real_process_output(mock_pwnam):
    proc = wrapper.start("/bin/echo", "testuser")
    proc.wait(timeout=5)
    for thread in proc.drain_threads:
        thread.join(timeout=5)
    assert any("--attach" in line for line in proc.stdout_tail)
