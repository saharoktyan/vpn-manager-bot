from __future__ import annotations

import os
import subprocess


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_int_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


BOT_TOKEN = _env_str("BOT_TOKEN")
ADMIN_IDS = _env_int_list("ADMIN_IDS")

BASE_DIR = _env_str("VPN_BOT_BASE_DIR", "/opt/vpn-bot")
APP_DIR = f"{BASE_DIR}/app"
DATA_DIR = f"{BASE_DIR}/data"

SUBS_DB_PATH = _env_str("SUBS_DB_PATH", f"{DATA_DIR}/subs.json")
USERS_DB_PATH = _env_str("USERS_DB_PATH", f"{DATA_DIR}/users.json")
WG_DB_PATH = _env_str("WG_DB_PATH", f"{DATA_DIR}/wg_db.json")
SQLITE_DB_PATH = _env_str("SQLITE_DB_PATH", f"{DATA_DIR}/bot.sqlite3")

SSH_KEY = _env_str("SSH_KEY")

PARSE_MODE = _env_str("PARSE_MODE", "Markdown")
MENU_TITLE = _env_str("MENU_TITLE", "VPN Bot")
CB_MENU = "menu:"
CB_GETKEY = "getkey:"
CB_CFG = "cfg:"
CB_SRV = "srv:"
LIST_PAGE_SIZE = _env_int("LIST_PAGE_SIZE", 12)


def _git_version() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


APP_VERSION = _env_str("APP_VERSION", _git_version())
