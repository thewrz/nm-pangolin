"""Thin wrapper around the Pangolin CLI binary.

All subprocess calls use argument lists (never shell=True) and absolute paths.
The service runs as root; pangolin commands run as the connecting user via runuser.
"""

import json
import logging
import os
import pwd
import shutil
import subprocess

log = logging.getLogger(__name__)

_SYSTEM_PATHS = ["/usr/bin/pangolin", "/usr/local/bin/pangolin"]


class PangolinNotFoundError(Exception):
    """Raised when the pangolin binary cannot be located."""


def _user_local_paths() -> list[str]:
    """Return ~/.local/bin/pangolin paths for all real (non-system) users."""
    paths = []
    for pw in pwd.getpwall():
        if pw.pw_uid >= 1000 and pw.pw_dir and os.path.isdir(pw.pw_dir):
            candidate = os.path.join(pw.pw_dir, ".local", "bin", "pangolin")
            paths.append(candidate)
    return paths


def find_pangolin() -> str:
    """Locate the pangolin binary on the system.

    Checks PATH via shutil.which, then system locations, then
    ~/.local/bin/ for all real users (common for user-local installs).

    Returns:
        Absolute path to the pangolin binary.

    Raises:
        PangolinNotFoundError: If the binary is not found anywhere.
    """
    found = shutil.which("pangolin")
    if found is not None:
        return os.path.realpath(found)

    for path in _SYSTEM_PATHS + _user_local_paths():
        if os.path.isfile(path) and os.access(path, os.X_OK):
            log.info("Found pangolin at %s", path)
            return path

    raise PangolinNotFoundError(
        "pangolin binary not found in PATH, system locations, or ~/.local/bin/"
    )


def _user_env(user: str) -> dict[str, str]:
    """Build a minimal environment dict for running commands as *user*.

    Sets HOME and XDG_CONFIG_HOME so pangolin can find its auth state.
    """
    try:
        pw = pwd.getpwnam(user)
    except KeyError as exc:
        raise ValueError(f"Unknown system user: {user!r}") from exc

    home = pw.pw_dir
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": home,
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "USER": user,
        "LOGNAME": user,
    }


def _run_as_user_cmd(user: str, pangolin_path: str, *args: str) -> list[str]:
    """Build a command list for executing pangolin as *user*.

    Runs pangolin directly with the user's environment variables set
    (HOME, XDG_CONFIG_HOME) so it finds the right auth state.
    The service runs as root, which has permission to read user config
    files and create TUN interfaces.
    """
    return [pangolin_path, *args]


def start(
    pangolin_path: str,
    user: str,
    org: str | None = None,
    iface: str | None = None,
    no_override_dns: bool = True,
) -> subprocess.Popen:
    """Start the pangolin tunnel as *user* (non-blocking).

    Returns the Popen handle so the caller can monitor the process.
    """
    args = ["up", "--silent"]

    if no_override_dns:
        args.append("--no-override-dns")
    if org is not None:
        args.extend(["--org", org])
    if iface is not None:
        args.extend(["--interface-name", iface])

    cmd = _run_as_user_cmd(user, pangolin_path, *args)
    env = _user_env(user)

    log.info("Starting pangolin: %s", " ".join(cmd))

    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
        close_fds=True,
    )


def stop(pangolin_path: str, user: str, timeout: int = 10) -> None:
    """Stop the pangolin tunnel synchronously.

    Raises subprocess.TimeoutExpired if the command does not finish
    within *timeout* seconds.
    """
    cmd = _run_as_user_cmd(user, pangolin_path, "down")
    env = _user_env(user)

    log.info("Stopping pangolin: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        close_fds=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        log.warning("pangolin down exited %d: %s", result.returncode, stderr)


def is_authenticated(pangolin_path: str, user: str, timeout: int = 5) -> bool:
    """Check if the user is authenticated with pangolin."""
    cmd = _run_as_user_cmd(user, pangolin_path, "auth", "status")
    env = _user_env(user)

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("pangolin auth status check failed: %s", exc)
        return False


def status(
    pangolin_path: str, user: str, timeout: int = 5
) -> dict | None:
    """Query pangolin status as JSON.

    Returns the parsed JSON dict, or None if the command fails or
    produces unparsable output.
    """
    cmd = _run_as_user_cmd(user, pangolin_path, "status", "--json")
    env = _user_env(user)

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("pangolin status timed out after %ds", timeout)
        return None

    if result.returncode != 0:
        log.debug(
            "pangolin status exited %d",
            result.returncode,
        )
        return None

    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Failed to parse pangolin status JSON: %s", exc)
        return None


def get_interface_config(iface: str = "pangolin") -> dict:
    """Read IP configuration from the pangolin TUN interface.

    Returns:
        Dict with keys: address (str), prefix (int),
        gateway (str | None), dns (list[str]).

    Raises:
        RuntimeError: If the interface cannot be queried.
    """
    try:
        addr_result = subprocess.run(
            ["ip", "-json", "addr", "show", iface],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timed out querying interface {iface!r}") from exc

    if addr_result.returncode != 0:
        stderr = addr_result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ip addr show {iface} failed: {stderr}")

    try:
        addr_data = json.loads(addr_result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Failed to parse ip addr output: {exc}") from exc

    address = None
    prefix = None
    for entry in addr_data:
        for info in entry.get("addr_info", []):
            if info.get("family") == "inet":
                address = info.get("local")
                prefix = info.get("prefixlen")
                break
        if address is not None:
            break

    if address is None:
        raise RuntimeError(f"No IPv4 address found on interface {iface!r}")

    # Query routes for gateway
    gateway = None
    try:
        route_result = subprocess.run(
            ["ip", "-json", "route", "show", "dev", iface],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            timeout=5,
        )
        if route_result.returncode == 0:
            routes = json.loads(route_result.stdout)
            for route in routes:
                gw = route.get("gateway")
                if gw is not None:
                    gateway = gw
                    break
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        log.warning("Failed to query routes for %s: %s", iface, exc)

    return {
        "address": address,
        "prefix": prefix,
        "gateway": gateway,
        "dns": [],
    }


def cleanup_orphans(
    pangolin_path: str, iface: str = "pangolin"
) -> None:
    """Kill stale pangolin processes and remove leftover TUN interfaces.

    Best-effort cleanup — errors are logged but not raised.
    """
    # Kill stale pangolin processes (exact binary match only)
    pangolin_name = os.path.basename(pangolin_path)
    try:
        result = subprocess.run(
            ["pgrep", "-x", pangolin_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            timeout=5,
        )
        if result.returncode == 0:
            pids = result.stdout.decode("utf-8", errors="replace").split()
            for raw_pid in pids:
                pid = raw_pid.strip()
                if pid:
                    log.info("Killing orphaned pangolin process %s", pid)
                    subprocess.run(
                        ["kill", "-TERM", pid],
                        close_fds=True,
                        timeout=5,
                    )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Failed to kill orphaned pangolin processes: %s", exc)

    # Remove stale TUN interface
    try:
        result = subprocess.run(
            ["ip", "link", "show", iface],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            timeout=5,
        )
        if result.returncode == 0:
            log.info("Removing stale interface %s", iface)
            subprocess.run(
                ["ip", "link", "delete", iface],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                timeout=5,
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Failed to remove stale interface %s: %s", iface, exc)
