from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Set

from config import SQLITE_DB_PATH, SUBS_DB_PATH, USERS_DB_PATH, WG_DB_PATH
from db.schema import ensure_schema
from db.sqlite_db import SQLiteDB
from domain.servers import get_access_method
from storage.json_store import JsonStore


@dataclass
class MigrationStats:
    profiles: int = 0
    subscriptions: int = 0
    access_methods: int = 0
    xray_profiles: int = 0
    xray_transports: int = 0
    awg_configs: int = 0
    telegram_users: int = 0


subs_store = JsonStore(SUBS_DB_PATH)
users_store = JsonStore(USERS_DB_PATH)
wg_store = JsonStore(WG_DB_PATH)


def _normalize_awg_entry(entry: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(entry, dict):
        return {}
    servers = entry.get("servers")
    if isinstance(servers, dict):
        return {
            str(region): server_entry
            for region, server_entry in servers.items()
            if isinstance(server_entry, dict)
        }
    region = entry.get("region")
    if isinstance(region, str) and region:
        return {region: entry}
    return {}


def _profile_names(subs: Dict[str, Any]) -> Set[str]:
    names: Set[str] = {name for name in subs.keys() if not str(name).startswith("_")}
    names.update(str(name) for name in wg_store.read().keys() if not str(name).startswith("_"))
    for raw_user in users_store.read().values():
        if isinstance(raw_user, dict):
            username = str(raw_user.get("username") or "").lstrip("@").strip()
            if username:
                names.add(username)
    return names


def migrate(sqlite_path: str = SQLITE_DB_PATH) -> MigrationStats:
    db = SQLiteDB(sqlite_path)
    subs = subs_store.read()
    users = users_store.read()
    profile_names = _profile_names(subs)
    stats = MigrationStats()

    with db.transaction() as conn:
        ensure_schema(conn)

        conn.execute("DELETE FROM profile_access_methods")
        conn.execute("DELETE FROM xray_transports")
        conn.execute("DELETE FROM xray_profiles")
        conn.execute("DELETE FROM awg_server_configs")
        conn.execute("DELETE FROM subscriptions")
        conn.execute("DELETE FROM profiles")
        conn.execute("DELETE FROM telegram_users")

        for name in sorted(profile_names):
            rec = subs.get(name)
            if not isinstance(rec, dict):
                rec = {}

            created_at = rec.get("created_at")
            updated_at = rec.get("updated_at") or created_at
            conn.execute(
                "INSERT INTO profiles(name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, created_at, updated_at),
            )
            stats.profiles += 1

            conn.execute(
                """
                INSERT INTO subscriptions(profile_name, subscription_type, created_at, expires_at, frozen, warned_before_exp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    rec.get("type", "none"),
                    created_at,
                    rec.get("expires_at"),
                    1 if rec.get("frozen") else 0,
                    1 if rec.get("warned_before_exp") else 0,
                ),
            )
            stats.subscriptions += 1

            protocols = rec.get("protocols")
            if not isinstance(protocols, list):
                protocols = []

            valid_protocols = [str(code) for code in protocols if get_access_method(str(code))]
            if not valid_protocols:
                xray = rec.get("xray")
                if isinstance(xray, dict) and xray.get("enabled"):
                    valid_protocols = ["gx"]

            for code in sorted(set(valid_protocols)):
                conn.execute(
                    "INSERT INTO profile_access_methods(profile_name, access_code) VALUES (?, ?)",
                    (name, code),
                )
                stats.access_methods += 1

            uuid_val = rec.get("uuid")
            xray = rec.get("xray")
            if uuid_val or isinstance(xray, dict):
                transports = ["xhttp", "tcp"]
                default_transport = "xhttp"
                enabled = 1
                short_id = None
                if isinstance(xray, dict):
                    raw_transports = xray.get("transports")
                    if isinstance(raw_transports, list) and raw_transports:
                        transports = [str(item) for item in raw_transports]
                    default_transport = str(xray.get("default") or default_transport)
                    enabled = 1 if xray.get("enabled", True) else 0
                    short_id = xray.get("short_id")

                conn.execute(
                    """
                    INSERT INTO xray_profiles(profile_name, uuid, enabled, short_id, default_transport)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, uuid_val, enabled, short_id, default_transport),
                )
                stats.xray_profiles += 1

                for transport in transports:
                    conn.execute(
                        "INSERT INTO xray_transports(profile_name, transport) VALUES (?, ?)",
                        (name, transport),
                    )
                    stats.xray_transports += 1

            for server_key, awg_entry in _normalize_awg_entry(wg_store.read().get(name)).items():
                conn.execute(
                    """
                    INSERT INTO awg_server_configs(profile_name, server_key, config_text, wg_conf, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        server_key,
                        awg_entry.get("config", ""),
                        awg_entry.get("wg_conf"),
                        awg_entry.get("created_at"),
                    ),
                )
                stats.awg_configs += 1

        for raw_user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            try:
                telegram_user_id = int(raw_user_id)
            except (TypeError, ValueError):
                continue
            conn.execute(
                """
                INSERT INTO telegram_users(
                    telegram_user_id, chat_id, username, first_name, last_name,
                    updated_at, last_key_at, key_issued_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    raw_user.get("chat_id"),
                    raw_user.get("username"),
                    raw_user.get("first_name"),
                    raw_user.get("last_name"),
                    raw_user.get("updated_at"),
                    raw_user.get("last_key_at"),
                    int(raw_user.get("key_issued_count") or 0),
                ),
            )
            stats.telegram_users += 1

    return stats


def main() -> None:
    stats = migrate()
    print("SQLite migration completed:")
    print(f"- profiles: {stats.profiles}")
    print(f"- subscriptions: {stats.subscriptions}")
    print(f"- access_methods: {stats.access_methods}")
    print(f"- xray_profiles: {stats.xray_profiles}")
    print(f"- xray_transports: {stats.xray_transports}")
    print(f"- awg_configs: {stats.awg_configs}")
    print(f"- telegram_users: {stats.telegram_users}")
    print(f"- sqlite_path: {SQLITE_DB_PATH}")


if __name__ == "__main__":
    main()
