# app/handlers/user_common.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, APP_VERSION, MENU_TITLE, PARSE_MODE
from domain.servers import get_access_methods_for_codes, get_tracked_awg_server_keys
from i18n import detect_locale, get_locale_for_update, t
from services.subscriptions import users_store
from utils.keyboards import kb_main_menu


def _touch_key_stat(context: CallbackContext, user_id: int) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def mut(db: Dict[str, Any]) -> Dict[str, Any]:
        rec = db.get(str(user_id))
        if not isinstance(rec, dict):
            rec = {}
        rec["last_key_at"] = now
        rec["key_issued_count"] = int(rec.get("key_issued_count") or 0) + 1
        db[str(user_id)] = rec
        return db

    users_store.update(mut)


def _parse_iso(dt_str: str):
    try:
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def _human_ago(iso: str) -> str:
    dt = _parse_iso(iso)
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    sec = int((now - dt).total_seconds())
    if sec < 0:
        sec = 0

    if sec < 60:
        return f"{sec} сек назад"
    if sec < 3600:
        return f"{sec // 60} мин назад"
    if sec < 86400:
        return f"{sec // 3600} ч назад"
    return f"{sec // 86400} дн назад"


def _progress_bar(p: float, width: int = 10) -> str:
    p = 0.0 if p < 0 else 1.0 if p > 1 else p
    filled = int(round(p * width))
    filled = max(0, min(width, filled))
    return "▰" * filled + "▱" * (width - filled)


def _sub_progress(created_iso: str, expires_iso: str) -> tuple[str, str]:
    c = _parse_iso(created_iso)
    e = _parse_iso(expires_iso)
    if not c or not e:
        return "—", "—"
    if c.tzinfo is None:
        c = c.replace(tzinfo=timezone.utc)
    if e.tzinfo is None:
        e = e.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    total = (e - c).total_seconds()
    left = (e - now).total_seconds()
    if total <= 0:
        return "—", "—"

    used = total - max(0, left)
    p = used / total
    return _progress_bar(p, 10), f"{int(round(p * 100))}%"


def _human_left(exp_iso: str) -> str:
    dt = _parse_iso(exp_iso)
    if not dt:
        return "—"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = int((dt - now).total_seconds())
    if sec <= 0:
        return "истекла"
    days = sec // 86400
    hrs = (sec % 86400) // 3600
    if days > 0:
        return f"{days} дн {hrs} ч"
    mins = (sec % 3600) // 60
    return f"{hrs} ч {mins} мин"


def _conf_msg_key(server_key: str) -> str:
    return f"last_awg_conf_msg_id:{server_key}"


def _delete_last_awg_conf(context: CallbackContext, chat_id: int, server_key: str) -> None:
    key = _conf_msg_key(server_key)
    mid = context.user_data.get(key)
    if not mid:
        return
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=int(mid))
    except Exception:
        pass
    context.user_data.pop(key, None)


def _delete_all_awg_conf(context: CallbackContext, chat_id: int) -> None:
    for server_key in get_tracked_awg_server_keys():
        _delete_last_awg_conf(context, chat_id, server_key)


def _is_admin(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    return bool(uid) and (uid in ADMIN_IDS)


def _build_getkey_items(codes: List[str]) -> List[Tuple[str, str]]:
    return [(method.getkey_payload, method.label) for method in get_access_methods_for_codes(codes)]


def start_cmd(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    def mut(db: Dict[str, Any]) -> Dict[str, Any]:
        db[str(user.id)] = {
            "chat_id": chat.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "locale": (db.get(str(user.id), {}) or {}).get("locale") or detect_locale(update),
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        return db

    users_store.update(mut)
    lang = get_locale_for_update(update)
    update.effective_message.reply_text(
        f"*{MENU_TITLE}*\n\n{t(lang, 'menu.choose_action')}",
        parse_mode=PARSE_MODE,
        reply_markup=kb_main_menu(_is_admin(update), lang),
        disable_web_page_preview=True,
    )


def whoami_cmd(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not user:
        return
    update.effective_message.reply_text(f"Your id: {user.id}\nusername: @{user.username or ''}")


def version_cmd(update: Update, context: CallbackContext) -> None:
    lang = get_locale_for_update(update)
    update.effective_message.reply_text(f"{MENU_TITLE}\n{t(lang, 'version.label', version=APP_VERSION)}")


def getkey_cmd(update: Update, context: CallbackContext) -> None:
    start_cmd(update, context)
