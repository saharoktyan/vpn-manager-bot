# app/services/xray.py
from __future__ import annotations

import logging
import shlex
import uuid as uuid_lib
from typing import Any, List, Optional, Tuple
from urllib.parse import quote

from services.server_registry import get_server, list_servers
from services.server_runtime import run_local_command, run_server_command

log = logging.getLogger("xray")

_cache: dict[str, dict[str, object]] = {}


def run_local(cmd: str, timeout: int = 60) -> Tuple[int, str]:
    return run_local_command(cmd, timeout=timeout)


def _cache_get(server_key: str, ttl: float) -> Optional[Tuple[int, List[str], str]]:
    import time

    item = _cache.get(server_key)
    if not item:
        return None
    if time.time() - float(item["ts"]) >= ttl:
        return None
    return int(item["code"]), list(item["names"]), str(item["raw"])


def _cache_set(server_key: str, code: int, names: List[str], raw: str) -> None:
    import time

    _cache[server_key] = {"ts": time.time(), "code": code, "names": list(names), "raw": raw}


def _default_xray_server_key() -> Optional[str]:
    for server in list_servers():
        if server.enabled and "xray" in server.protocol_kinds:
            return server.key
    return None


def get_uuid_local(name: str) -> Optional[str]:
    from services.subscriptions import get_profile

    rec = get_profile(name)
    uuid_val = rec.get("uuid") if isinstance(rec, dict) else None
    return str(uuid_val) if isinstance(uuid_val, str) and uuid_val.strip() else None


def add_user(name: str, server_key: Optional[str] = None, uuid_value: Optional[str] = None) -> Tuple[int, str]:
    server_key = server_key or _default_xray_server_key()
    if not server_key:
        return 1, "No Xray servers are registered"
    server = get_server(server_key)
    if not server:
        return 1, f"Server {server_key} not found"

    if uuid_value:
        cmd = f"/opt/vpn-manager-node/xray-add-user-existing.sh {shlex.quote(name)} {shlex.quote(uuid_value)}"
    else:
        cmd = f"echo {shlex.quote(name)} | /opt/vpn-manager-node/xray-add-user.sh"
    return run_server_command(server, cmd, timeout=120)


def list_users(server_key: Optional[str] = None) -> Tuple[int, List[str], str]:
    server_key = server_key or _default_xray_server_key()
    if not server_key:
        return 1, [], "No Xray servers are registered"
    server = get_server(server_key)
    if not server:
        return 1, [], f"Server {server_key} not found"
    code, out = run_server_command(server, "/opt/vpn-manager-node/xray-list-users.sh", timeout=60)
    if code != 0:
        return code, [], out
    lines = out.strip().splitlines()
    if not lines:
        return 0, [], out
    names: List[str] = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            names.append(parts[0])
    return 0, names, out


def list_user_records(server_key: Optional[str] = None) -> Tuple[int, List[dict[str, Any]], str]:
    server_key = server_key or _default_xray_server_key()
    if not server_key:
        return 1, [], "No Xray servers are registered"
    server = get_server(server_key)
    if not server:
        return 1, [], f"Server {server_key} not found"
    code, out = run_server_command(server, "/opt/vpn-manager-node/xray-list-users.sh", timeout=60)
    if code != 0:
        return code, [], out
    lines = out.strip().splitlines()
    if not lines:
        return 0, [], out
    items: List[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2:
            items.append({"name": parts[0], "uuid": parts[1]})
        elif parts:
            items.append({"name": parts[0], "uuid": None})
    return 0, items, out


def list_users_cached(server_key: str, ttl: float = 3.0) -> Tuple[int, List[str], str]:
    cached = _cache_get(server_key, ttl)
    if cached is not None:
        return cached
    code, names, raw = list_users(server_key)
    _cache_set(server_key, code, names, raw)
    return code, names, raw


def get_uuid_by_name(name: str, server_key: Optional[str] = None) -> Optional[str]:
    local_uuid = get_uuid_local(name)
    if local_uuid:
        return local_uuid
    server_key = server_key or _default_xray_server_key()
    if not server_key:
        return None
    code, _names, raw = list_users_cached(server_key, ttl=3.0)
    if code != 0:
        return None
    lines = raw.strip().splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return parts[1]
    return None


def ensure_user(name: str, server_key: str, uuid_value: Optional[str] = None) -> Tuple[int, str, Optional[str]]:
    uuid_value = uuid_value or get_uuid_local(name) or str(uuid_lib.uuid4())
    code, out = add_user(name, server_key=server_key, uuid_value=uuid_value)
    if code != 0:
        lower_out = (out or "").lower()
        if "already exists" in lower_out or "exists" in lower_out or "duplicate" in lower_out:
            return 0, out, uuid_value
        return code, out, None
    return 0, out, uuid_value


def delete_user(name: str, server_key: Optional[str] = None) -> Tuple[int, str]:
    server_key = server_key or _default_xray_server_key()
    if not server_key:
        return 1, "No Xray servers are registered"
    server = get_server(server_key)
    if not server:
        return 1, f"Server {server_key} not found"
    cmd = f"/opt/vpn-manager-node/xray-del-user.sh {shlex.quote(name)}"
    return run_server_command(server, cmd, timeout=120)


def build_vless_link_transport(name: str, uuid: str, transport: str, server_key: str) -> str:
    server = get_server(server_key)
    if not server:
        raise KeyError(server_key)

    ready, reason = get_server_link_status(server_key)
    if not ready:
        raise ValueError(reason)

    short_id = server.xray_short_id or server.xray_sid
    path_prefix = server.xray_xhttp_path_prefix or "/assets"

    if transport == "xhttp":
        suffix = f"{path_prefix}/{short_id}".rstrip("/")
        path = quote(suffix if suffix else path_prefix, safe="")
        return (
            f"vless://{uuid}@{server.xray_host}:{server.xray_xhttp_port}"
            f"?encryption=none"
            f"&security=reality"
            f"&sni={server.xray_sni}"
            f"&fp={server.xray_fp}"
            f"&pbk={server.xray_pbk}"
            f"&sid={server.xray_sid}"
            f"&type=xhttp"
            f"&path={path}"
            f"#reality-{server.key}-{name}-xhttp"
        )

    return (
        f"vless://{uuid}@{server.xray_host}:{server.xray_tcp_port}"
        f"?encryption=none"
        f"&security=reality"
        f"&sni={server.xray_sni}"
        f"&fp={server.xray_fp}"
        f"&pbk={server.xray_pbk}"
        f"&sid={server.xray_sid}"
        f"&type=tcp"
        f"&flow={server.xray_flow}"
        f"#reality-{server.key}-{name}-tcp"
    )


def get_server_link_status(server_key: str) -> tuple[bool, str]:
    server = get_server(server_key)
    if not server:
        return False, f"Server {server_key} not found"

    missing: list[str] = []
    if not server.xray_host:
        missing.append("xray_host")
    if not server.xray_sni:
        missing.append("xray_sni")
    if not server.xray_pbk:
        missing.append("xray_pbk")
    if not server.xray_sid:
        missing.append("xray_sid")

    if missing:
        return False, f"Xray link settings are incomplete for server {server_key}: {', '.join(missing)}"
    return True, "ok"
