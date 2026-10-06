"""Thin wrapper around the Pangolin CLI binary.

All subprocess calls use argument lists (never shell=True) and absolute paths.
The service runs as root; pangolin commands run as the connecting user via runuser.
"""

import json
import logging
import os
import pwd
import shutil
import stat
import subprocess
import threading
from collections import deque

log = logging.getLogger(__name__)

# Text markers meaning "the CLI has no usable session". Checked against
# combined stdout+stderr because `pangolin auth status` exits 0 even when
# the session is expired (it prints "Failed to fetch user data: Unauthorized").
# Keep in sync with outputLooksUnauthenticated in plasma-plugin/pangolinauth.cpp.
UNAUTH_MARKERS = (b"unauthorized", b"not logged in", b"no account")

# auth_state() results. AUTH_NO requires positive evidence (a marker above);
# probe timeouts and execution errors are AUTH_UNKNOWN, never AUTH_NO, so a
# transient failure is not misreported to the user as bad credentials.
AUTH_YES = "yes"
AUTH_NO = "no"
AUTH_UNKNOWN = "unknown"

# How many trailing output lines to keep per stream for diagnostics.
OUTPUT_TAIL_LINES = 60

_SYSTEM_PATHS = ["/usr/bin/pangolin", "/usr/local/bin/pangolin"]


class PangolinNotFoundError(Exception):
    """Raised when the pangolin binary cannot be located."""


def _untrusted_reason(path: str) -> str | None:
    """Why root must not execute *path*; None when it is safe to.

    The service runs as root and launches this binary as root, so it has to
    be one that only root can replace: a regular file owned by root and not
    world-writable, reached through directories that are the same. A copy
    under a home directory fails this by definition -- whoever owns it could
    swap it and get root at the next connect.

    Group-writable is tolerated: a root-owned system directory with an admin
    group (Debian ships /usr/local as root:staff 2775) is the distribution's
    own trust decision.
    """
    current = os.path.realpath(path)
    try:
        st = os.stat(current)
    except OSError as exc:
        return f"{current}: {exc.strerror or exc}"
    if not stat.S_ISREG(st.st_mode):
        return f"{current} is not a regular file"
    if not os.access(current, os.X_OK):
        return f"{current} is not executable"

    while True:
        if st.st_uid != 0:
            return f"{current} is owned by uid {st.st_uid}, not root"
        if st.st_mode & stat.S_IWOTH:
            return f"{current} is world-writable"
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
        try:
            st = os.stat(current)
        except OSError as exc:
            return f"{current}: {exc.strerror or exc}"


def find_pangolin() -> str:
    """Locate a pangolin binary that root may execute.

    Checks PATH via shutil.which, then the system locations. Candidates that
    exist but could be replaced by a non-root user are skipped (see
    _untrusted_reason) and named in the error if nothing better is found.

    Returns:
        Absolute, symlink-resolved path to the pangolin binary.

    Raises:
        PangolinNotFoundError: If no trustworthy binary is found.
    """
    candidates = []
    found = shutil.which("pangolin")
    if found is not None:
        candidates.append(found)
    candidates.extend(p for p in _SYSTEM_PATHS if p not in candidates)

    rejected = []
    for path in candidates:
        try:
            os.stat(path)
        except OSError:
            continue
        reason = _untrusted_reason(path)
        if reason is None:
            return os.path.realpath(path)
        rejected.append(f"{path}: {reason}")

    if rejected:
        raise PangolinNotFoundError(
            "no pangolin binary that root may execute ("
            + "; ".join(rejected)
            + "). Install it root-owned at /usr/local/bin/pangolin -- "
            "install.sh does this."
        )
    raise PangolinNotFoundError(
        "pangolin binary not found in PATH or system locations"
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
    args = ["up", "--attach"]

    if no_override_dns:
        args.append("--override-dns=false")
    if org is not None:
        args.extend(["--org", org])
    if iface is not None:
        args.extend(["--interface-name", iface])

    cmd = _run_as_user_cmd(user, pangolin_path, *args)
    env = _user_env(user)

    log.info("Starting pangolin: %s (env: HOME=%s USER=%s)", " ".join(cmd), env.get("HOME"), env.get("USER"))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    # Drain both pipes continuously. In --attach mode pangolin logs for the
    # lifetime of the tunnel; an unread PIPE buffer fills (~64KB) and then
    # blocks the tunnel process mid-session. The tails also give the service
    # real diagnostics when the process exits.
    try:
        proc.stdout_tail, out_thread = _spawn_drain(proc.stdout)
        proc.stderr_tail, err_thread = _spawn_drain(proc.stderr)
    except RuntimeError as exc:
        # Thread startup failed: don't leak a live, untracked tunnel process.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise OSError(f"could not start pipe-drain threads: {exc}") from exc
    proc.drain_threads = tuple(t for t in (out_thread, err_thread) if t is not None)
    return proc


def _spawn_drain(stream) -> tuple[deque, "threading.Thread | None"]:
    """Read *stream* to EOF on a daemon thread, keeping the last lines."""
    tail: deque = deque(maxlen=OUTPUT_TAIL_LINES)
    if stream is None:
        return tail, None

    def _drain():
        try:
            for line in iter(stream.readline, b""):
                tail.append(line.decode("utf-8", errors="replace").rstrip())
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return tail, thread


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


def auth_state(pangolin_path: str, user: str, timeout: int = 5) -> str:
    """Classify the CLI auth state: AUTH_YES, AUTH_NO, or AUTH_UNKNOWN.

    The exit code alone is NOT trustworthy: `pangolin auth status` exits 0
    even when the session has expired, printing "Failed to fetch user data:
    Unauthorized" instead. The output text is authoritative. AUTH_NO is
    returned only on positive evidence; anything inconclusive (timeout,
    exec error, unexpected exit) is AUTH_UNKNOWN.
    """
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
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("pangolin auth status check failed: %s", exc)
        return AUTH_UNKNOWN

    output = (result.stdout + result.stderr).lower()
    if any(marker in output for marker in UNAUTH_MARKERS):
        return AUTH_NO
    if result.returncode == 0:
        return AUTH_YES
    return AUTH_UNKNOWN


def is_authenticated(pangolin_path: str, user: str, timeout: int = 5) -> bool:
    """True only when auth_state() has positive evidence of a session."""
    return auth_state(pangolin_path, user, timeout=timeout) == AUTH_YES


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
        log.debug("pangolin status exited %d", result.returncode)
        return None

    # The CLI prints an update-notice banner on stdout ahead of the JSON
    # payload whenever a newer release exists. raw_decode from each brace
    # candidate tolerates both leading text (braces in the banner included)
    # and trailing text after the object.
    text = result.stdout.decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)

    log.debug("pangolin status: no JSON object in output")
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
