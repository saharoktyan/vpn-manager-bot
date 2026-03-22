# app/services/subscriptions.py
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import SQLITE_DB_PATH, SUBS_DB_PATH, USERS_DB_PATH, WG_DB_PATH
from db.migrate_from_json import migrate as migrate_json_to_sqlite
from db.schema import ensure_schema
from db.sqlite_db import SQLiteDB
from db.stores import SQLiteAWGStore, SQLiteSubscriptionsStore, SQLiteTelegramUsersStore
from domain.servers import get_access_method, get_access_methods_for_kind
from storage.json_store import JsonStore

logger = logging.getLogger(__name__)

_sqlite_db = SQLiteDB(SQLITE_DB_PATH)
_wg_json_store = JsonStore(WG_DB_PATH)


def _sqlite_counts() -> Tuple[int, int, int]:
    if not os.path.exists(SQLITE_DB_PATH):
        return 0, 0, 0
    with _sqlite_db.connect() as conn:
        profile_count = int(conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()["c"])
        user_count = int(conn.execute("SELECT COUNT(*) AS c FROM telegram_users").fetchone()["c"])
        awg_count = int(conn.execute("SELECT COUNT(*) AS c FROM awg_server_configs").fetchone()["c"])
        return profile_count, user_count, awg_count


def _bootstrap_sqlite_runtime() -> None:
    with _sqlite_db.transaction() as conn:
        ensure_schema(conn)
    profile_count, user_count, awg_count = _sqlite_counts()
    json_profiles = len([name for name in JsonStore(SUBS_DB_PATH).read().keys() if not str(name).startswith("_")])
    json_users = len(JsonStore(USERS_DB_PATH).read())
    json_awg = len(_wg_json_store.read())
    if profile_count == 0 and json_profiles > 0:
        logger.info("Bootstrapping SQLite from JSON: importing profiles/subscriptions/users")
        migrate_json_to_sqlite(SQLITE_DB_PATH)
        return
    if user_count == 0 and json_users > 0:
        logger.info("Bootstrapping SQLite from JSON: importing telegram users")
        migrate_json_to_sqlite(SQLITE_DB_PATH)
        return
    if awg_count == 0 and json_awg > 0:
        logger.info("Bootstrapping SQLite from JSON: importing AWG configs")
        migrate_json_to_sqlite(SQLITE_DB_PATH)


_bootstrap_sqlite_runtime()

subs_store = SQLiteSubscriptionsStore(_sqlite_db)
users_store = SQLiteTelegramUsersStore(_sqlite_db)
wg_store = SQLiteAWGStore(_sqlite_db)

_AWG_VPN_RE = re.compile(r"(vpn://[A-Za-z0-9+/=_-]+)")

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def format_delta(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        total = 0
    days = total // 86400
    hours = (total % 86400) // 3600
    mins = (total % 3600) // 60
    if days > 0:
        return f"{days}д {hours}ч"
    if hours > 0:
        return f"{hours}ч {mins}м"
    return f"{mins}м"


def get_profile(name: str) -> Dict[str, Any]:
    data = subs_store.read()
    rec = data.get(name)
    return rec if isinstance(rec, dict) else {}


def is_frozen(name: str) -> bool:
    return bool(get_profile(name).get("frozen", False))


def get_subscription_status(name: str) -> Dict[str, Any]:
    rec = get_profile(name)
    if not rec:
        # no record => unlimited (by your older logic)
        return {"active": True, "frozen": False, "text": "Подписка: *бессрочная* (по умолчанию)"}

    frozen = bool(rec.get("frozen", False))
    t = rec.get("type", "none")
    exp = rec.get("expires_at")

    if t in ("none", "lifetime") or not exp:
        txt = "Подписка: *бессрочная*"
        return {"active": True, "frozen": frozen, "text": txt + ("\nСтатус: *заморожена* 🧊" if frozen else "")}

    try:
        exp_dt = datetime.fromisoformat(exp)
    except Exception:
        return {"active": True, "frozen": frozen, "text": "Подписка: *неизвестна* (ошибка даты)"}

    now = utcnow()
    if exp_dt <= now:
        return {"active": False, "frozen": frozen, "text": "Подписка: *истекла* ❌"}

    left = exp_dt - now
    return {
        "active": True,
        "frozen": frozen,
        "text": f"Подписка: *активна* ✅\nОсталось: *{format_delta(left)}*\nДо: `{exp_dt.isoformat(timespec='minutes')}`"
               + ("\nСтатус: *заморожена* 🧊" if frozen else ""),
    }


def freeze_profile(name: str) -> Tuple[bool, str]:
    def mut(d: Dict[str, Any]) -> Dict[str, Any]:
        rec = d.get(name)
        if not isinstance(rec, dict):
            rec = {"type": "none", "created_at": utcnow().isoformat(timespec="minutes"), "expires_at": None}
        rec["frozen"] = True
        d[name] = rec
        return d
    subs_store.update(mut)
    return True, f"🧊 Профиль *{name}* заморожен."


def unfreeze_profile(name: str) -> Tuple[bool, str]:
    def mut(d: Dict[str, Any]) -> Dict[str, Any]:
        rec = d.get(name)
        if not isinstance(rec, dict):
            return d
        rec["frozen"] = False
        d[name] = rec
        return d
    subs_store.update(mut)
    return True, f"🔥 Профиль *{name}* разморожен."


def get_allowed_protocols(name: str) -> List[str]:
    rec = get_profile(name)
    plist = rec.get("protocols")
    if isinstance(plist, list) and plist:
        return [str(x) for x in plist if get_access_method(str(x))]
    # fallback: derive from caps
    x = rec.get("xray")
    if isinstance(x, dict) and x.get("enabled"):
        methods = get_access_methods_for_kind("xray")
        if methods:
            return [methods[0].code]
    return []


def ensure_xray_caps(name: str, uuid_val: str) -> None:
    def mut(s: Dict[str, Any]) -> Dict[str, Any]:
        rec = s.get(name)
        if not isinstance(rec, dict):
            rec = {}
        rec.setdefault("type", "none")
        rec.setdefault("created_at", utcnow().isoformat(timespec="minutes"))
        rec.setdefault("expires_at", None)
        rec.setdefault("frozen", False)
        rec.setdefault("warned_before_exp", False)

        rec["uuid"] = uuid_val
        x = rec.get("xray")
        if not isinstance(x, dict):
            x = {}
        x.setdefault("enabled", True)
        x.setdefault("transports", ["xhttp", "tcp"])
        x.setdefault("default", "xhttp")
        x.setdefault("short_id", "")
        rec["xray"] = x

        s[name] = rec
        return s
    subs_store.update(mut)


def set_xray_short_id(name: str, short_id: str) -> None:
    def mut(s: Dict[str, Any]) -> Dict[str, Any]:
        rec = s.get(name)
        if not isinstance(rec, dict):
            rec = {}
        x = rec.get("xray")
        if not isinstance(x, dict):
            x = {}
        x["short_id"] = short_id
        rec["xray"] = x
        s[name] = rec
        return s

    subs_store.update(mut)


def auto_freeze_job(_context) -> None:
    # placeholder: keep for future; you can implement auto-freeze/notify here
    return

def _extract_vpn_key(text: str) -> str | None:
    if not text:
        return None
    m = _AWG_VPN_RE.search(text)
    return m.group(1) if m else None
