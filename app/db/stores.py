from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from db.sqlite_db import SQLiteDB


def _decode_xray_short_id(value: Any) -> tuple[str, Dict[str, str]]:
    raw = str(value or "").strip()
    if not raw:
        return "", {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:
            return raw, {}
        if isinstance(data, dict):
            mapping = {
                str(server_key): str(short_id).strip()
                for server_key, short_id in data.items()
                if str(server_key).strip() and str(short_id).strip()
            }
            return "", mapping
    return raw, {}


def _encode_xray_short_id(xray: Dict[str, Any] | None) -> str | None:
    if not isinstance(xray, dict):
        return None
    mapping = xray.get("server_short_ids")
    if isinstance(mapping, dict):
        normalized = {
            str(server_key): str(short_id).strip()
            for server_key, short_id in mapping.items()
            if str(server_key).strip() and str(short_id).strip()
        }
        if normalized:
            return json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    short_id = str(xray.get("short_id") or "").strip()
    return short_id or None


class SQLiteSubscriptionsStore:
    def __init__(self, db: SQLiteDB) -> None:
        self.db = db

    def read(self) -> Dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.name,
                    p.created_at AS profile_created_at,
                    p.updated_at,
                    s.subscription_type,
                    s.created_at AS subscription_created_at,
                    s.expires_at,
                    s.frozen,
                    s.warned_before_exp,
                    x.uuid,
                    x.enabled AS xray_enabled,
                    x.short_id,
                    x.default_transport
                FROM profiles p
                LEFT JOIN subscriptions s ON s.profile_name = p.name
                LEFT JOIN xray_profiles x ON x.profile_name = p.name
                ORDER BY p.name
                """
            ).fetchall()

            result: Dict[str, Any] = {}
            for row in rows:
                name = str(row["name"])
                rec: Dict[str, Any] = {
                    "type": row["subscription_type"] or "none",
                    "created_at": row["subscription_created_at"] or row["profile_created_at"],
                    "expires_at": row["expires_at"],
                    "frozen": bool(row["frozen"]) if row["frozen"] is not None else False,
                    "warned_before_exp": bool(row["warned_before_exp"]) if row["warned_before_exp"] is not None else False,
                    "updated_at": row["updated_at"],
                }
                if row["uuid"] is not None:
                    short_id, server_short_ids = _decode_xray_short_id(row["short_id"])
                    transports = [
                        str(item["transport"])
                        for item in conn.execute(
                            "SELECT transport FROM xray_transports WHERE profile_name = ? ORDER BY transport",
                            (name,),
                        ).fetchall()
                    ]
                    rec["uuid"] = row["uuid"]
                    rec["xray"] = {
                        "enabled": bool(row["xray_enabled"]) if row["xray_enabled"] is not None else True,
                        "transports": transports or ["tcp", "xhttp"],
                        "short_id": short_id,
                        "server_short_ids": server_short_ids,
                        "default": row["default_transport"] or "xhttp",
                    }
                access_codes = [
                    str(item["access_code"])
                    for item in conn.execute(
                        "SELECT access_code FROM profile_access_methods WHERE profile_name = ? ORDER BY access_code",
                        (name,),
                    ).fetchall()
                ]
                if access_codes:
                    rec["protocols"] = access_codes
                result[name] = rec
            return result

    def write(self, data: Dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            names: List[str] = [
                str(name)
                for name in sorted(data.keys())
                if isinstance(data.get(name), dict)
            ]
            conn.execute("DELETE FROM profile_access_methods")
            conn.execute("DELETE FROM xray_transports")
            conn.execute("DELETE FROM xray_profiles")
            conn.execute("DELETE FROM subscriptions")
            if names:
                placeholders = ",".join("?" for _ in names)
                conn.execute(
                    f"DELETE FROM profiles WHERE name NOT IN ({placeholders})",
                    names,
                )
            else:
                conn.execute("DELETE FROM profiles")

            for name in names:
                rec = data.get(name)
                created_at = rec.get("created_at")
                updated_at = rec.get("updated_at") or created_at
                conn.execute(
                    """
                    INSERT INTO profiles(name, created_at, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (name, created_at, updated_at),
                )
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
                protocols = rec.get("protocols")
                if isinstance(protocols, list):
                    for code in sorted({str(item) for item in protocols}):
                        conn.execute(
                            "INSERT INTO profile_access_methods(profile_name, access_code) VALUES (?, ?)",
                            (name, code),
                        )
                xray = rec.get("xray")
                uuid_val = rec.get("uuid")
                if uuid_val is not None or isinstance(xray, dict):
                    transports = ["xhttp", "tcp"]
                    default_transport = "xhttp"
                    enabled = True
                    short_id = None
                    if isinstance(xray, dict):
                        raw_transports = xray.get("transports")
                        if isinstance(raw_transports, list) and raw_transports:
                            transports = [str(item) for item in raw_transports]
                        default_transport = str(xray.get("default") or default_transport)
                        enabled = bool(xray.get("enabled", True))
                        short_id = _encode_xray_short_id(xray)
                    conn.execute(
                        """
                        INSERT INTO xray_profiles(profile_name, uuid, enabled, short_id, default_transport)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (name, uuid_val, 1 if enabled else 0, short_id, default_transport),
                    )
                    for transport in sorted(set(transports)):
                        conn.execute(
                            "INSERT INTO xray_transports(profile_name, transport) VALUES (?, ?)",
                            (name, transport),
                        )

    def update(self, mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
        data = self.read()
        new_data = mutator(data)
        if not isinstance(new_data, dict):
            raise ValueError("SQLiteSubscriptionsStore.update mutator must return dict")
        self.write(new_data)
        return new_data


class SQLiteTelegramUsersStore:
    def __init__(self, db: SQLiteDB) -> None:
        self.db = db

    def read(self) -> Dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_user_id, chat_id, username, first_name, last_name, profile_name, locale,
                       access_granted, access_request_pending, access_request_sent_at,
                       notify_access_requests, telemetry_enabled,
                       updated_at, last_key_at, key_issued_count
                FROM telegram_users
                ORDER BY telegram_user_id
                """
            ).fetchall()
            result: Dict[str, Any] = {}
            for row in rows:
                result[str(row["telegram_user_id"])] = {
                    "chat_id": row["chat_id"],
                    "username": row["username"] or "",
                    "first_name": row["first_name"] or "",
                    "last_name": row["last_name"] or "",
                    "profile_name": row["profile_name"],
                    "locale": row["locale"] or "ru",
                    "access_granted": bool(row["access_granted"]) if row["access_granted"] is not None else False,
                    "access_request_pending": bool(row["access_request_pending"]) if row["access_request_pending"] is not None else False,
                    "access_request_sent_at": row["access_request_sent_at"],
                    "notify_access_requests": bool(row["notify_access_requests"]) if row["notify_access_requests"] is not None else True,
                    "telemetry_enabled": bool(row["telemetry_enabled"]) if row["telemetry_enabled"] is not None else False,
                    "updated_at": row["updated_at"],
                    "last_key_at": row["last_key_at"],
                    "key_issued_count": int(row["key_issued_count"] or 0),
                }
            return result

    def write(self, data: Dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM telegram_users")
            for raw_user_id in sorted(data.keys(), key=lambda value: int(value) if str(value).isdigit() else str(value)):
                rec = data.get(raw_user_id)
                if not isinstance(rec, dict):
                    continue
                try:
                    telegram_user_id = int(raw_user_id)
                except (TypeError, ValueError):
                    continue
                conn.execute(
                    """
                    INSERT INTO telegram_users(
                        telegram_user_id, chat_id, username, first_name, last_name, profile_name,
                        locale, access_granted, access_request_pending, access_request_sent_at,
                        notify_access_requests, telemetry_enabled,
                        updated_at, last_key_at, key_issued_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_user_id,
                        rec.get("chat_id"),
                        rec.get("username"),
                        rec.get("first_name"),
                        rec.get("last_name"),
                        rec.get("profile_name"),
                        rec.get("locale") or "ru",
                        1 if rec.get("access_granted") else 0,
                        1 if rec.get("access_request_pending") else 0,
                        rec.get("access_request_sent_at"),
                        1 if rec.get("notify_access_requests", True) else 0,
                        1 if rec.get("telemetry_enabled") else 0,
                        rec.get("updated_at"),
                        rec.get("last_key_at"),
                        int(rec.get("key_issued_count") or 0),
                    ),
                )

    def update(self, mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
        data = self.read()
        new_data = mutator(data)
        if not isinstance(new_data, dict):
            raise ValueError("SQLiteTelegramUsersStore.update mutator must return dict")
        self.write(new_data)
        return new_data


class SQLiteAWGStore:
    def __init__(self, db: SQLiteDB) -> None:
        self.db = db

    def read(self) -> Dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT profile_name, server_key, config_text, wg_conf, created_at
                FROM awg_server_configs
                ORDER BY profile_name, server_key
                """
            ).fetchall()
            result: Dict[str, Any] = {}
            for row in rows:
                profile_name = str(row["profile_name"])
                profile = result.setdefault(profile_name, {"servers": {}})
                profile["servers"][str(row["server_key"])] = {
                    "server_key": row["server_key"],
                    "config": row["config_text"] or "",
                    "wg_conf": row["wg_conf"],
                    "created_at": row["created_at"],
                }
            return result

    def write(self, data: Dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM awg_server_configs")
            for profile_name in sorted(data.keys()):
                profile = data.get(profile_name)
                if not isinstance(profile, dict):
                    continue
                servers = profile.get("servers")
                if not isinstance(servers, dict):
                    server_key = profile.get("server_key") or profile.get("region")
                    if isinstance(server_key, str) and server_key:
                        servers = {server_key: profile}
                    else:
                        servers = {}
                for server_key, server_entry in servers.items():
                    if not isinstance(server_entry, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO awg_server_configs(profile_name, server_key, config_text, wg_conf, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            profile_name,
                            str(server_key),
                            str(server_entry.get("config") or ""),
                            server_entry.get("wg_conf"),
                            server_entry.get("created_at"),
                        ),
                    )

    def update(self, mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
        data = self.read()
        new_data = mutator(data)
        if not isinstance(new_data, dict):
            raise ValueError("SQLiteAWGStore.update mutator must return dict")
        self.write(new_data)
        return new_data
