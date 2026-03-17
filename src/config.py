"""Parse and validate NetworkManager connection settings for Pangolin VPN.

All string values are validated against strict patterns before they can
reach subprocess calls.  Provides sensible defaults for optional fields.
"""

import logging
import pwd
import re
from typing import Any

log = logging.getLogger(__name__)

_SAFE_STRING = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
_URL_PATTERN = re.compile(r"^https?://[a-zA-Z0-9._:/-]+$")

_DEFAULT_INTERFACE = "pangolin"


class ConfigError(Exception):
    """Raised when connection configuration is missing or invalid."""


def validate_string(
    value: str,
    name: str,
    pattern: re.Pattern = _SAFE_STRING,
) -> str:
    """Validate *value* against *pattern*.

    Returns the validated string unchanged.

    Raises:
        ConfigError: If *value* does not match *pattern*.
    """
    if not isinstance(value, str):
        raise ConfigError(f"{name}: expected string, got {type(value).__name__}")
    if not pattern.match(value):
        raise ConfigError(f"{name}: invalid value {value!r}")
    return value


def _extract_vpn_data(connection: dict) -> dict[str, str]:
    """Return the vpn.data mapping from the NM connection dict."""
    vpn = connection.get("vpn", {})
    return dict(vpn.get("data", {}))


def _extract_vpn_secrets(connection: dict) -> dict[str, str]:
    """Return the vpn.secrets mapping from the NM connection dict."""
    vpn = connection.get("vpn", {})
    return dict(vpn.get("secrets", {}))


def parse_connection(connection: dict[str, Any]) -> dict[str, Any]:
    """Parse NM's connection settings into validated config values.

    Extracts and validates fields from vpn.data, vpn.secrets, and
    connection metadata.

    Returns:
        A new dict with keys: server_url, org, interface_name, mtu,
        olm_id, olm_secret, user.

    Raises:
        ConfigError: For missing required fields or invalid values.
    """
    data = _extract_vpn_data(connection)
    secrets = _extract_vpn_secrets(connection)

    # --- required ---
    server_url = data.get("server-url")
    if not server_url:
        raise ConfigError("server-url is required in vpn.data")
    server_url = validate_string(server_url, "server-url", _URL_PATTERN)

    # --- optional (vpn.data) ---
    org = data.get("org")
    if org is not None:
        org = validate_string(org, "org")

    interface_name = data.get("interface-name", _DEFAULT_INTERFACE)
    interface_name = validate_string(interface_name, "interface-name")

    mtu: int | None = None
    raw_mtu = data.get("mtu")
    if raw_mtu is not None:
        try:
            mtu = int(raw_mtu)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"mtu: expected positive integer, got {raw_mtu!r}") from exc
        if mtu <= 0:
            raise ConfigError(f"mtu: must be positive, got {mtu}")

    # --- optional (vpn.secrets) ---
    olm_id = secrets.get("olm-id")
    if olm_id is not None:
        olm_id = validate_string(olm_id, "olm-id")

    olm_secret = secrets.get("olm-secret")
    if olm_secret is not None:
        olm_secret = validate_string(olm_secret, "olm-secret")

    user = get_connecting_user(connection)

    return {
        "server_url": server_url,
        "org": org,
        "interface_name": interface_name,
        "mtu": mtu,
        "olm_id": olm_id,
        "olm_secret": olm_secret,
        "user": user,
    }


def get_connecting_user(connection: dict[str, Any]) -> str:
    """Extract the connecting user from NM connection properties.

    Looks for a user-name or UID in the connection metadata and resolves
    it to a valid system username.

    Raises:
        ConfigError: If the user cannot be determined or does not exist.
    """
    conn_settings = connection.get("connection", {})

    # NM may store permissions as "user:username:" entries
    permissions = conn_settings.get("permissions", [])
    for perm in permissions:
        if isinstance(perm, str) and perm.startswith("user:"):
            parts = perm.split(":")
            if len(parts) >= 2 and parts[1]:
                username = parts[1]
                return _resolve_user(username)

    # Fallback: look for explicit user field
    username = conn_settings.get("user")
    if username:
        return _resolve_user(username)

    # Fallback: look for a UID
    uid = conn_settings.get("uid")
    if uid is not None:
        try:
            pw = pwd.getpwuid(int(uid))
            return pw.pw_name
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"Cannot resolve UID {uid}") from exc

    raise ConfigError(
        "Cannot determine connecting user from connection properties"
    )


def _resolve_user(username: str) -> str:
    """Validate that *username* exists on the system and return it."""
    validated = validate_string(username, "user")
    if validated == "root":
        raise ConfigError("Refusing to run pangolin as root")
    try:
        pwd.getpwnam(validated)
    except KeyError as exc:
        raise ConfigError(f"System user {validated!r} does not exist") from exc
    return validated
