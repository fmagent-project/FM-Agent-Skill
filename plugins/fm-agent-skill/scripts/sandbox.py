"""Minimal, shared sandbox construction for Coordinator-owned adapters.

Neither build detection nor dynamic reproduction may inherit the host root,
home directory, environment, or network.  This module deliberately accepts an
argv that has already been selected by a local adapter; it never parses an
Agent-provided command string.
"""
from __future__ import annotations

from pathlib import Path
import shutil


# A runtime in a user home directory is never safe to mount merely to make a
# language adapter work.  Provision optional runtimes in /opt/fm-agent-runtime
# (or a sealed container image) instead.
SAFE_RUNTIME_ROOTS = (
    Path("/usr/bin"), Path("/usr/local/bin"), Path("/usr/local/go"),
    Path("/opt/fm-agent-runtime"),
)
SAFE_READONLY_DIRS = (
    Path("/usr"), Path("/lib"), Path("/lib64"), Path("/opt/fm-agent-runtime"),
)


class AdapterUnavailable(ValueError):
    """The requested adapter cannot execute within the approved runtime."""


def _approved_executable(command: list[str]) -> None:
    if not command or not isinstance(command[0], str):
        raise AdapterUnavailable("adapter did not provide a fixed executable")
    executable = Path(command[0])
    if not executable.is_absolute():
        resolved = shutil.which(command[0])
        if not resolved:
            raise AdapterUnavailable(f"required command is unavailable: {command[0]}")
        executable = Path(resolved)
    executable = executable.resolve()
    if not executable.is_file() or not any(executable.is_relative_to(root) for root in SAFE_RUNTIME_ROOTS):
        raise AdapterUnavailable(
            "approved runtime must be installed below /usr/bin, /usr/local/bin, "
            "/usr/local/go, or /opt/fm-agent-runtime; user-home runtimes are not mounted"
        )


def sandbox_command(target: Path, scratch: Path, command: list[str], adapter_env: dict[str, str] | None = None) -> list[str]:
    """Return Bubblewrap argv for a fixed adapter command.

    ``scratch`` is Coordinator-created and attempt-specific.  It is the sole
    writable mount and is intentionally bound at /tmp so adapters can use a
    deterministic temporary path without seeing the host temporary directory.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise AdapterUnavailable("bubblewrap is required; unsafe adapter execution is disabled")
    _approved_executable(command)
    scratch.mkdir(parents=True, exist_ok=True)
    result = [bwrap, "--die-with-parent", "--new-session", "--unshare-net", "--unshare-pid", "--clearenv"]
    mounted: list[Path] = []
    for directory in (item for item in SAFE_READONLY_DIRS if item.is_dir()):
        parent_is_available = directory.parent == Path("/") or any(
            directory.parent == prior or directory.parent.is_relative_to(prior) for prior in mounted
        )
        if not parent_is_available:
            result += ["--dir", str(directory.parent)]
        result += ["--ro-bind", str(directory), str(directory)]
        mounted.append(directory)
    result += [
        "--dir", "/project", "--ro-bind", str(target), "/project",
        "--bind", str(scratch), "/tmp", "--proc", "/proc", "--dev", "/dev",
        "--chdir", "/project", "--setenv", "HOME", "/tmp/home",
        "--setenv", "TMPDIR", "/tmp", "--setenv", "PATH",
        "/usr/bin:/usr/local/bin:/usr/local/go/bin:/opt/fm-agent-runtime",
    ]
    for key, value in (adapter_env or {}).items():
        if not key.startswith("FM_AGENT_") and key not in {"CARGO_HOME", "CARGO_TARGET_DIR", "CARGO_NET_OFFLINE", "GOCACHE", "GOMODCACHE", "GOPROXY", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"}:
            raise AdapterUnavailable(f"adapter attempted to set an unapproved environment variable: {key}")
        result += ["--setenv", key, value]
    return [*result, "--", *command]


def sandbox_metadata() -> dict[str, str]:
    return {
        "engine": "bubblewrap",
        "network": "disabled",
        "project": "read-only",
        "tmp": "private",
        "host_root": "not-mounted",
        "host_home": "not-mounted",
    }
