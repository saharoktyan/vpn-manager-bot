from __future__ import annotations

from config import MENU_TITLE, SQLITE_DB_PATH
from db.schema import ensure_schema
from db.sqlite_db import SQLiteDB


_db = SQLiteDB(SQLITE_DB_PATH)
_GLOBAL_TELEMETRY_KEY = "telemetry_enabled_global"
_BOT_MENU_TITLE_KEY = "bot_menu_title"
_schema_ready = False


def _ensure_runtime_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _db.transaction() as conn:
        ensure_schema(conn)
    _schema_ready = True


def is_global_telemetry_enabled() -> bool:
    _ensure_runtime_schema()
    with _db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (_GLOBAL_TELEMETRY_KEY,),
        ).fetchone()
    return bool(row) and str(row["value"]).strip() == "1"


def set_global_telemetry_enabled(enabled: bool) -> bool:
    _ensure_runtime_schema()
    value = "1" if enabled else "0"
    with _db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            (_GLOBAL_TELEMETRY_KEY, value),
        )
    return enabled


def get_menu_title() -> str:
    _ensure_runtime_schema()
    with _db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (_BOT_MENU_TITLE_KEY,),
        ).fetchone()
    value = str(row["value"]).strip() if row and row["value"] is not None else ""
    return value or MENU_TITLE


def set_menu_title(value: str) -> str:
    _ensure_runtime_schema()
    normalized = str(value or "").strip() or MENU_TITLE
    with _db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            (_BOT_MENU_TITLE_KEY, normalized),
        )
    return normalized
