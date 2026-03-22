from __future__ import annotations

import base64
import logging
import os
import shlex
import subprocess
import time
from typing import Tuple

from services.server_registry import RegisteredServer
from services.ssh_keys import ensure_ssh_keypair


log = logging.getLogger("server_runtime")


def is_running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as fh:
            data = fh.read()
        return any(token in data for token in ("docker", "containerd", "kubepods"))
    except Exception:
        return False


def run_local_command(cmd: str, timeout: int = 60) -> Tuple[int, str]:
    t0 = time.time()
    log.info("RUN: %s", cmd)
    try:
        proc = subprocess.run(
            ["/usr/bin/bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        log.info("DONE rc=%s sec=%.2f", proc.returncode, time.time() - t0)
        if out.strip():
            log.debug("OUT: %s", out.strip()[:1500])
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as exc:
        log.exception("Command failed: %s", exc)
        return 1, f"Exception: {exc}"


def _ssh_command(server: RegisteredServer, command: str) -> str:
    target = server.ssh_target
    if not target:
        raise ValueError(f"SSH target is not configured for server {server.key}")
    opts = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=2",
        "-p",
        str(server.ssh_port or 22),
    ]
    if server.ssh_key_path:
        ok, err = ensure_ssh_keypair(server.ssh_key_path)
        if not ok:
            raise ValueError(f"Could not prepare SSH keypair at {server.ssh_key_path}: {err}")
        opts.extend(["-i", server.ssh_key_path])
    opts.append(target)
    opts.append(f"bash -lc {shlex.quote(command)}")
    return " ".join(shlex.quote(part) for part in opts)


def run_server_command(server: RegisteredServer, command: str, timeout: int = 60) -> Tuple[int, str]:
    if server.transport == "local":
        if is_running_in_container():
            return (
                1,
                "Local transport is unavailable while the bot runs inside a container. "
                "Register this node with transport=ssh and point it to the host system instead.",
            )
        return run_local_command(command, timeout=timeout)
    return run_local_command(_ssh_command(server, command), timeout=timeout)


def write_server_file(server: RegisteredServer, path: str, content: str, mode: str = "0644") -> Tuple[int, str]:
    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
    parent = path.rsplit("/", 1)[0] if "/" in path else "."
    cmd = (
        f"mkdir -p {shlex.quote(parent)} && "
        f"python3 - <<'PY'\n"
        f"import base64, pathlib\n"
        f"data = base64.b64decode({payload!r})\n"
        f"path = pathlib.Path({path!r})\n"
        f"path.write_bytes(data)\n"
        f"PY\n"
        f"chmod {mode} {shlex.quote(path)}"
    )
    return run_server_command(server, cmd, timeout=60)
