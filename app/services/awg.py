# app/services/awg.py
from __future__ import annotations

import logging
import re
import shlex
from typing import Dict, List, Tuple

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


def extract_client_public_key(wg_conf: str) -> str | None:
    if not wg_conf:
        return None
    for raw_line in wg_conf.splitlines():
        line = raw_line.strip()
        if line.startswith("PublicKey = "):
            return line.split("=", 1)[1].strip() or None
    return None


def list_awg_peer_transfers(server_key: str) -> Tuple[int, List[Dict[str, int | str]], str]:
    server = get_server(server_key)
    if not server:
        return 1, [], f"Unknown server: {server_key}"
    cmd = (
        "source /etc/vpn-bot/node.env && "
        'CONTAINER="${AWG_CONTAINER_NAME:-amnezia-awg}" && '
        f'IFACE="{server.awg_iface}" && '
        'if docker info >/dev/null 2>&1; then DOCKER="docker"; '
        'elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then DOCKER="sudo docker"; '
        'else echo "Docker is not available for this user." >&2; exit 1; fi && '
        '$DOCKER exec -i "$CONTAINER" sh -lc "wg show $IFACE transfer"'
    )
    code, out = run_server_command(server, cmd, timeout=60)
    if code != 0:
        return code, [], out

    records: List[Dict[str, int | str]] = []
    for raw_line in (out or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        peer_key = parts[0].strip()
        try:
            rx_bytes = int(parts[1])
            tx_bytes = int(parts[2])
        except ValueError:
            continue
        records.append({"peer_key": peer_key, "rx_bytes_total": rx_bytes, "tx_bytes_total": tx_bytes})
    return 0, records, out
