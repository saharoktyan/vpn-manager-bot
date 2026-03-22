from __future__ import annotations

import sqlite3
from typing import Iterable


BASE_DDL: Iterable[str] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profiles (
        name TEXT PRIMARY KEY,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        profile_name TEXT PRIMARY KEY,
        subscription_type TEXT NOT NULL DEFAULT 'none',
        created_at TEXT,
        expires_at TEXT,
        frozen INTEGER NOT NULL DEFAULT 0,
        warned_before_exp INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (profile_name) REFERENCES profiles(name) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_access_methods (
        profile_name TEXT NOT NULL,
        access_code TEXT NOT NULL,
        PRIMARY KEY (profile_name, access_code),
        FOREIGN KEY (profile_name) REFERENCES profiles(name) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xray_profiles (
        profile_name TEXT PRIMARY KEY,
        uuid TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        default_transport TEXT,
        FOREIGN KEY (profile_name) REFERENCES profiles(name) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xray_transports (
        profile_name TEXT NOT NULL,
        transport TEXT NOT NULL,
        PRIMARY KEY (profile_name, transport),
        FOREIGN KEY (profile_name) REFERENCES profiles(name) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_users (
        telegram_user_id INTEGER PRIMARY KEY,
        chat_id INTEGER,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        locale TEXT NOT NULL DEFAULT 'ru',
        updated_at TEXT,
        last_key_at TEXT,
        key_issued_count INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_access_methods_profile
    ON profile_access_methods(profile_name)
    """,
)


SERVERS_DDL = """
CREATE TABLE IF NOT EXISTS servers (
    key TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    title TEXT NOT NULL,
    flag TEXT NOT NULL,
    transport TEXT NOT NULL,
    public_host TEXT,
    protocol_kinds TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    ssh_host TEXT,
    ssh_port INTEGER NOT NULL DEFAULT 22,
    ssh_user TEXT,
    ssh_key_path TEXT,
    bootstrap_state TEXT NOT NULL DEFAULT 'new',
    notes TEXT,
    xray_config_path TEXT,
    xray_service_name TEXT,
    xray_host TEXT,
    xray_sni TEXT,
    xray_pbk TEXT,
    xray_sid TEXT,
    xray_short_id TEXT,
    xray_fp TEXT,
    xray_flow TEXT,
    xray_tcp_port INTEGER,
    xray_xhttp_port INTEGER,
    xray_xhttp_path_prefix TEXT,
    awg_config_path TEXT,
    awg_iface TEXT,
    awg_public_host TEXT,
    awg_port INTEGER,
    created_at TEXT,
    updated_at TEXT
)
"""


AWG_DDL = """
CREATE TABLE IF NOT EXISTS awg_server_configs (
    profile_name TEXT NOT NULL,
    server_key TEXT NOT NULL,
    config_text TEXT NOT NULL,
    wg_conf TEXT,
    created_at TEXT,
    PRIMARY KEY (profile_name, server_key),
    FOREIGN KEY (profile_name) REFERENCES profiles(name) ON DELETE CASCADE
)
"""


PROFILE_SERVER_STATE_DDL = """
CREATE TABLE IF NOT EXISTS profile_server_state (
    profile_name TEXT NOT NULL,
    server_key TEXT NOT NULL,
    protocol_kind TEXT NOT NULL,
    desired_enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    remote_id TEXT,
    last_error TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (profile_name, server_key, protocol_kind),
    FOREIGN KEY (profile_name) REFERENCES profiles(name) ON DELETE CASCADE,
    FOREIGN KEY (server_key) REFERENCES servers(key) ON DELETE CASCADE
)
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    if not _table_exists(conn, name):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def _create_servers_table(conn: sqlite3.Connection) -> None:
    conn.execute(SERVERS_DDL)


def _migrate_telegram_users_table(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "telegram_users")
    if columns and "locale" not in columns:
        conn.execute("ALTER TABLE telegram_users ADD COLUMN locale TEXT NOT NULL DEFAULT 'ru'")


def _migrate_servers_table(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "servers")
    if not columns:
        _create_servers_table(conn)
        return

    desired = {
        "key",
        "region",
        "title",
        "flag",
        "transport",
        "public_host",
        "protocol_kinds",
        "enabled",
        "ssh_host",
        "ssh_port",
        "ssh_user",
        "ssh_key_path",
        "bootstrap_state",
        "notes",
        "xray_config_path",
        "xray_service_name",
        "xray_host",
        "xray_sni",
        "xray_pbk",
        "xray_sid",
        "xray_short_id",
        "xray_fp",
        "xray_flow",
        "xray_tcp_port",
        "xray_xhttp_port",
        "xray_xhttp_path_prefix",
        "awg_config_path",
        "awg_iface",
        "awg_public_host",
        "awg_port",
        "created_at",
        "updated_at",
    }
    if columns == desired:
        return

    conn.execute("ALTER TABLE servers RENAME TO servers_old")
    _create_servers_table(conn)

    old_rows = conn.execute("SELECT * FROM servers_old").fetchall()
    for row in old_rows:
        row_map = dict(row)
        key = str(row_map.get("key") or "")
        region = str(row_map.get("region") or key)
        protocol_kinds = row_map.get("protocol_kinds")
        if not protocol_kinds:
            if key == "de":
                protocol_kinds = "xray,awg"
            elif key == "lv":
                protocol_kinds = "awg"
            else:
                protocol_kinds = ""
        public_host = row_map.get("public_host") or row_map.get("ssh_host")
        conn.execute(
            """
            INSERT INTO servers(
                key, region, title, flag, transport, public_host, protocol_kinds, enabled,
                ssh_host, ssh_port, ssh_user, ssh_key_path, bootstrap_state, notes,
                xray_config_path, xray_service_name, xray_host, xray_sni, xray_pbk,
                xray_sid, xray_short_id, xray_fp, xray_flow, xray_tcp_port, xray_xhttp_port,
                xray_xhttp_path_prefix, awg_config_path, awg_iface, awg_public_host, awg_port,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                region,
                row_map.get("title") or key,
                row_map.get("flag") or "🏳️",
                row_map.get("transport") or "ssh",
                public_host,
                protocol_kinds,
                int(row_map.get("enabled", 1) or 1),
                row_map.get("ssh_host"),
                int(row_map.get("ssh_port") or 22),
                row_map.get("ssh_user"),
                row_map.get("ssh_key_path"),
                row_map.get("bootstrap_state") or "legacy",
                row_map.get("notes"),
                row_map.get("xray_config_path") or "/opt/vpn-manager-node/xray/config.json",
                row_map.get("xray_service_name") or "xray",
                row_map.get("xray_host"),
                row_map.get("xray_sni"),
                row_map.get("xray_pbk"),
                row_map.get("xray_sid"),
                row_map.get("xray_short_id"),
                row_map.get("xray_fp") or "chrome",
                row_map.get("xray_flow") or "xtls-rprx-vision",
                row_map.get("xray_tcp_port") or 443,
                row_map.get("xray_xhttp_port") or 8443,
                row_map.get("xray_xhttp_path_prefix") or "/assets",
                row_map.get("awg_config_path") or "/opt/vpn-manager-node/amnezia-awg/data/wg0.conf",
                row_map.get("awg_iface") or "wg0",
                row_map.get("awg_public_host") or public_host,
                row_map.get("awg_port") or 51820,
                row_map.get("created_at"),
                row_map.get("updated_at"),
            ),
        )

    conn.execute("DROP TABLE servers_old")


def _create_awg_table(conn: sqlite3.Connection) -> None:
    conn.execute(AWG_DDL)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_awg_server_configs_profile
        ON awg_server_configs(profile_name)
        """
    )


def _migrate_awg_table(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "awg_server_configs")
    if not columns:
        _create_awg_table(conn)
        return
    if "server_key" in columns:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_awg_server_configs_profile
            ON awg_server_configs(profile_name)
            """
        )
        return

    conn.execute("ALTER TABLE awg_server_configs RENAME TO awg_server_configs_old")
    _create_awg_table(conn)
    conn.execute(
        """
        INSERT INTO awg_server_configs(profile_name, server_key, config_text, wg_conf, created_at)
        SELECT profile_name, region, config_text, wg_conf, created_at
        FROM awg_server_configs_old
        """
    )
    conn.execute("DROP TABLE awg_server_configs_old")


def _create_profile_server_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(PROFILE_SERVER_STATE_DDL)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_server_state_profile
        ON profile_server_state(profile_name)
        """
    )


def _migrate_profile_server_state_table(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "profile_server_state")
    if not columns:
        _create_profile_server_state_table(conn)
        return
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_server_state_profile
        ON profile_server_state(profile_name)
        """
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    for ddl in BASE_DDL:
        conn.execute(ddl)
    _migrate_telegram_users_table(conn)
    _migrate_servers_table(conn)
    _migrate_awg_table(conn)
    _migrate_profile_server_state_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '3')"
    )
