"""Tests for config module."""

from unittest.mock import MagicMock, patch

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import config
from config import ConfigError, parse_connection, get_connecting_user, validate_string


def _make_connection(data=None, secrets=None, conn=None):
    """Build a minimal NM connection dict."""
    result = {}
    if data is not None or secrets is not None:
        vpn = {}
        if data is not None:
            vpn["data"] = data
        if secrets is not None:
            vpn["secrets"] = secrets
        result["vpn"] = vpn
    if conn is not None:
        result["connection"] = conn
    return result


@pytest.fixture
def mock_pwnam():
    pw = MagicMock()
    pw.pw_dir = "/home/testuser"
    pw.pw_name = "testuser"
    with patch("config.pwd.getpwnam", return_value=pw) as m:
        yield m


# --- parse_connection ---

def test_parse_connection_valid(mock_pwnam):
    conn = _make_connection(
        data={"server-url": "https://vpn.example.com", "org": "myorg", "interface-name": "tun0", "mtu": "1400"},
        secrets={"olm-id": "abc123", "olm-secret": "secret123"},
        conn={"permissions": ["user:testuser:"]},
    )
    result = parse_connection(conn)
    assert result["server_url"] == "https://vpn.example.com"
    assert result["org"] == "myorg"
    assert result["interface_name"] == "tun0"
    assert result["mtu"] == 1400
    assert result["full_tunnel"] is False
    assert result["user"] == "testuser"


def test_parse_connection_full_tunnel(mock_pwnam):
    conn = _make_connection(
        data={"server-url": "https://vpn.example.com", "full-tunnel": "true"},
        conn={"permissions": ["user:testuser:"]},
    )
    result = parse_connection(conn)
    assert result["full_tunnel"] is True


def test_parse_connection_full_tunnel_false(mock_pwnam):
    conn = _make_connection(
        data={"server-url": "https://vpn.example.com", "full-tunnel": "false"},
        conn={"permissions": ["user:testuser:"]},
    )
    result = parse_connection(conn)
    assert result["full_tunnel"] is False


def test_parse_connection_minimal(mock_pwnam):
    conn = _make_connection(
        data={"server-url": "https://vpn.example.com"},
        conn={"permissions": ["user:testuser:"]},
    )
    result = parse_connection(conn)
    assert result["server_url"] == "https://vpn.example.com"
    assert result["org"] is None
    assert result["interface_name"] == "pangolin"
    assert result["mtu"] is None
    assert result["full_tunnel"] is False


def test_parse_connection_missing_server_url(mock_pwnam):
    conn = _make_connection(data={}, conn={"permissions": ["user:testuser:"]})
    with pytest.raises(ConfigError, match="server-url is required"):
        parse_connection(conn)


def test_parse_connection_invalid_server_url(mock_pwnam):
    conn = _make_connection(
        data={"server-url": "https://evil.com; rm -rf /"},
        conn={"permissions": ["user:testuser:"]},
    )
    with pytest.raises(ConfigError, match="server-url"):
        parse_connection(conn)


def test_parse_connection_invalid_org(mock_pwnam):
    conn = _make_connection(
        data={"server-url": "https://vpn.example.com", "org": "my;org"},
        conn={"permissions": ["user:testuser:"]},
    )
    with pytest.raises(ConfigError, match="org"):
        parse_connection(conn)


@pytest.mark.parametrize("bad_mtu", ["abc", "-1", "0"])
def test_parse_connection_invalid_mtu(mock_pwnam, bad_mtu):
    conn = _make_connection(
        data={"server-url": "https://vpn.example.com", "mtu": bad_mtu},
        conn={"permissions": ["user:testuser:"]},
    )
    with pytest.raises(ConfigError, match="mtu"):
        parse_connection(conn)


# --- validate_string ---

def test_validate_string_valid():
    assert validate_string("hello-world.test_123", "field") == "hello-world.test_123"


def test_validate_string_rejects_leading_dash():
    with pytest.raises(ConfigError):
        validate_string("-batch", "field")


def test_validate_string_rejects_leading_dot():
    with pytest.raises(ConfigError):
        validate_string(".hidden", "field")


def test_get_connecting_user_rejects_root():
    with patch("config.pwd.getpwnam") as mock_pw:
        conn = {"connection": {"permissions": ["user:root:"]}}
        with pytest.raises(ConfigError, match="root"):
            get_connecting_user(conn)


@pytest.mark.parametrize("bad_input", [
    "cmd; rm -rf /",
    "foo|bar",
    "$(whoami)",
    "`whoami`",
    "a && b",
    "hello world",
])
def test_validate_string_rejects_injection(bad_input):
    with pytest.raises(ConfigError):
        validate_string(bad_input, "field")


# --- get_connecting_user ---

def test_get_connecting_user_from_permissions(mock_pwnam):
    conn = {"connection": {"permissions": ["user:testuser:"]}}
    assert get_connecting_user(conn) == "testuser"


def test_get_connecting_user_from_user_field(mock_pwnam):
    conn = {"connection": {"user": "bob"}}
    mock_pwnam.return_value.pw_name = "bob"
    assert get_connecting_user(conn) == "bob"


def test_get_connecting_user_from_uid():
    pw = MagicMock()
    pw.pw_name = "charlie"
    with patch("config.pwd.getpwuid", return_value=pw):
        conn = {"connection": {"uid": 1000}}
        assert get_connecting_user(conn) == "charlie"


def test_get_connecting_user_fallback_pangolin_config():
    pw = MagicMock()
    pw.pw_uid = 1000
    pw.pw_dir = "/home/testuser"
    pw.pw_name = "testuser"
    with patch("config.pwd.getpwall", return_value=[pw]), \
         patch("config.os.path.isdir", return_value=True):
        conn = {"connection": {}}
        assert get_connecting_user(conn) == "testuser"


def test_get_connecting_user_missing():
    pw = MagicMock()
    pw.pw_uid = 1000
    pw.pw_dir = "/home/testuser"
    pw.pw_name = "testuser"
    with patch("config.pwd.getpwall", return_value=[pw]), \
         patch("config.os.path.isdir", return_value=False):
        conn = {"connection": {}}
        with pytest.raises(ConfigError, match="Cannot determine connecting user"):
            get_connecting_user(conn)


def test_get_connecting_user_nonexistent():
    with patch("config.pwd.getpwnam", side_effect=KeyError("no such user")):
        conn = {"connection": {"permissions": ["user:ghost:"]}}
        with pytest.raises(ConfigError, match="does not exist"):
            get_connecting_user(conn)
