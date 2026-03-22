from __future__ import annotations

import argparse

from config import SQLITE_DB_PATH
from db.migrate_from_json import migrate
from db.schema import ensure_schema
from db.sqlite_db import SQLiteDB


def _counts(db: SQLiteDB) -> dict[str, int]:
    with db.connect() as conn:
        return {
            "servers": int(conn.execute("SELECT COUNT(*) AS c FROM servers").fetchone()["c"]),
            "profiles": int(conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()["c"]),
            "subscriptions": int(conn.execute("SELECT COUNT(*) AS c FROM subscriptions").fetchone()["c"]),
            "access_methods": int(conn.execute("SELECT COUNT(*) AS c FROM profile_access_methods").fetchone()["c"]),
            "xray_profiles": int(conn.execute("SELECT COUNT(*) AS c FROM xray_profiles").fetchone()["c"]),
            "xray_transports": int(conn.execute("SELECT COUNT(*) AS c FROM xray_transports").fetchone()["c"]),
            "awg_configs": int(conn.execute("SELECT COUNT(*) AS c FROM awg_server_configs").fetchone()["c"]),
            "telegram_users": int(conn.execute("SELECT COUNT(*) AS c FROM telegram_users").fetchone()["c"]),
        }


def cmd_init() -> None:
    db = SQLiteDB(SQLITE_DB_PATH)
    with db.transaction() as conn:
        ensure_schema(conn)
    print(f"SQLite schema initialized: {SQLITE_DB_PATH}")


def cmd_migrate() -> None:
    stats = migrate(SQLITE_DB_PATH)
    print("SQLite migration completed:")
    print(f"- profiles: {stats.profiles}")
    print(f"- subscriptions: {stats.subscriptions}")
    print(f"- access_methods: {stats.access_methods}")
    print(f"- xray_profiles: {stats.xray_profiles}")
    print(f"- xray_transports: {stats.xray_transports}")
    print(f"- awg_configs: {stats.awg_configs}")
    print(f"- telegram_users: {stats.telegram_users}")
    print(f"- sqlite_path: {SQLITE_DB_PATH}")


def cmd_status() -> None:
    db = SQLiteDB(SQLITE_DB_PATH)
    with db.transaction() as conn:
        ensure_schema(conn)
    counts = _counts(db)
    print(f"SQLite status: {SQLITE_DB_PATH}")
    for key, value in counts.items():
        print(f"- {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage vpn-bot v2 SQLite database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create SQLite schema if it does not exist")
    subparsers.add_parser("migrate", help="Import data from JSON files into SQLite")
    subparsers.add_parser("status", help="Show current SQLite table counts")

    args = parser.parse_args()
    if args.command == "init":
        cmd_init()
    elif args.command == "migrate":
        cmd_migrate()
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
