from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from config import SQLITE_DB_PATH
from db.schema import ensure_schema
from db.sqlite_db import SQLiteDB
from services.app_settings import is_global_telemetry_enabled
from services.awg import extract_client_public_key, list_awg_peer_transfers
from services.server_registry import list_servers


log = logging.getLogger("traffic_usage")
_db = SQLiteDB(SQLITE_DB_PATH)


def _ensure_runtime_schema() -> None:
    with _db.transaction() as conn:
        ensure_schema(conn)


_ensure_runtime_schema()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month_start_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def record_traffic_sample(
    profile_name: str,
    server_key: str,
    protocol_kind: str,
    remote_id: str,
    rx_bytes_total: int,
    tx_bytes_total: int,
    sampled_at: str,
) -> None:
    with _db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO traffic_samples(
                profile_name, server_key, protocol_kind, remote_id,
                rx_bytes_total, tx_bytes_total, sampled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_name,
                server_key,
                protocol_kind,
                remote_id,
                int(rx_bytes_total),
                int(tx_bytes_total),
                sampled_at,
            ),
        )


def _collect_awg_server_samples(server_key: str) -> tuple[int, str]:
    if not is_global_telemetry_enabled():
        return 0, "telemetry disabled globally"
    code, records, raw = list_awg_peer_transfers(server_key)
    if code != 0:
        return code, raw

    peer_map = {
        str(item.get("peer_key") or ""): item
        for item in records
        if str(item.get("peer_key") or "")
    }
    sampled_at = _now_iso()
    collected = 0

    with _db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT profile_name, wg_conf
            FROM awg_server_configs cfg
            JOIN telegram_users tu ON tu.profile_name = cfg.profile_name
            WHERE cfg.server_key = ?
              AND tu.telemetry_enabled = 1
            ORDER BY profile_name
            """,
            (server_key,),
        ).fetchall()

        for row in rows:
            profile_name = str(row["profile_name"])
            peer_key = extract_client_public_key(str(row["wg_conf"] or ""))
            if not peer_key:
                continue
            item = peer_map.get(peer_key)
            if not item:
                continue
            conn.execute(
                """
                INSERT INTO traffic_samples(
                    profile_name, server_key, protocol_kind, remote_id,
                    rx_bytes_total, tx_bytes_total, sampled_at
                ) VALUES (?, ?, 'awg', ?, ?, ?, ?)
                """,
                (
                    profile_name,
                    server_key,
                    peer_key,
                    int(item["rx_bytes_total"]),
                    int(item["tx_bytes_total"]),
                    sampled_at,
                ),
            )
            collected += 1

    return 0, f"server={server_key}\nsamples={collected}"


def collect_awg_traffic_samples() -> tuple[int, str]:
    if not is_global_telemetry_enabled():
        return 0, "telemetry disabled globally"
    blocks: list[str] = []
    errors = 0
    for server in list_servers():
        if "awg" not in server.protocol_kinds:
            continue
        if server.bootstrap_state != "bootstrapped":
            continue
        code, out = _collect_awg_server_samples(server.key)
        blocks.append(out)
        if code != 0:
            errors += 1
            log.warning("AWG traffic sampling failed for %s: %s", server.key, out)
    return (1 if errors else 0), "\n\n".join(blocks) if blocks else "no awg servers to sample"


def collect_traffic_job(_context: Any) -> None:
    code, out = collect_awg_traffic_samples()
    if code != 0:
        log.warning("Traffic sampling finished with errors:\n%s", out)
    else:
        log.info("Traffic sampling completed:\n%s", out)


def get_profile_monthly_usage(profile_name: str, protocol_kind: str = "awg") -> Dict[str, int]:
    if not is_global_telemetry_enabled():
        return {"rx_bytes": 0, "tx_bytes": 0, "total_bytes": 0, "samples": 0, "peers": 0}
    month_start = _month_start_iso()
    with _db.connect() as conn:
        rows = conn.execute(
            """
            SELECT server_key, remote_id, rx_bytes_total, tx_bytes_total, sampled_at
            FROM traffic_samples
            WHERE profile_name = ? AND protocol_kind = ? AND sampled_at >= ?
            ORDER BY server_key, remote_id, sampled_at
            """,
            (profile_name, protocol_kind, month_start),
        ).fetchall()

    totals = {"rx_bytes": 0, "tx_bytes": 0, "total_bytes": 0, "samples": 0, "peers": 0}
    groups: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["server_key"]), str(row["remote_id"]))
        groups.setdefault(key, []).append(dict(row))

    totals["samples"] = len(rows)
    totals["peers"] = len(groups)

    for samples in groups.values():
        if not samples:
            continue
        first = samples[0]
        last = samples[-1]
        rx_delta = max(0, int(last["rx_bytes_total"]) - int(first["rx_bytes_total"]))
        tx_delta = max(0, int(last["tx_bytes_total"]) - int(first["tx_bytes_total"]))
        totals["rx_bytes"] += rx_delta
        totals["tx_bytes"] += tx_delta

    totals["total_bytes"] = totals["rx_bytes"] + totals["tx_bytes"]
    return totals
