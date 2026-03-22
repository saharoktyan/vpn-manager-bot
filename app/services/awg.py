# app/services/awg.py
from __future__ import annotations

import logging
import re
import shlex
from typing import Tuple

from services.server_registry import get_server
from services.server_runtime import run_server_command

log = logging.getLogger("awg")


def create_awg_user(server_key: str, name: str) -> Tuple[int, str, str]:
    server = get_server(server_key)
    if not server:
        return 1, "", f"Unknown server: {server_key}"
    code, out = run_server_command(server, f"/opt/vpn-manager-node/awg-add-user.sh {shlex.quote(name)}", timeout=120)
    log.info("AWG create server=%s name=%s rc=%s", server_key, name, code)
    return code, out, out


def delete_awg_user(server_key: str, name: str) -> Tuple[int, str]:
    server = get_server(server_key)
    if not server:
        return 1, f"Unknown server: {server_key}"
    code, out = run_server_command(server, f"/opt/vpn-manager-node/awg-del-user.sh {shlex.quote(name)}", timeout=120)
    log.info("AWG delete server=%s name=%s rc=%s", server_key, name, code)
    return code, out


_WG_CONF_RE = re.compile(r"(\[Interface\][\s\S]*?\n\[Peer\][\s\S]*?)(?:\n=+|\Z)")


def _extract_wg_conf(text: str) -> str | None:
    if not text:
        return None
    m = _WG_CONF_RE.search(text)
    if not m:
        return None
    conf = m.group(1).strip()
    return conf.replace("\r\n", "\n").replace("\r", "\n")
