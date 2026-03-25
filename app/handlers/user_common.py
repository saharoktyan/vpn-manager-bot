# app/handlers/user_common.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, APP_VERSION, PARSE_MODE
from domain.servers import get_access_methods_for_codes, get_tracked_awg_server_keys
from i18n import detect_locale, get_locale_for_update, t
from services.app_settings import get_menu_title
from services.subscriptions import get_profile, subs_store, users_store
from utils.keyboards import kb_main_menu


def _touch_key_stat(context: CallbackContext, user_id: int) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    users_store.bump_key_stat(user_id, now)


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


def _has_access(update: Update) -> bool:
    if _is_admin(update):
        return True
    user = update.effective_user
    if not user:
        return False
    db = users_store.read()
    urec = db.get(str(user.id)) if isinstance(db, dict) else None
    if isinstance(urec, dict) and urec.get("access_granted"):
        return True
    profile_name = _resolve_profile_name(user.id)
    if not profile_name:
        return False
    rec = get_profile(profile_name)
    return bool(rec)


def _build_getkey_items(codes: List[str]) -> List[Tuple[str, str]]:
    return [(method.getkey_payload, method.label) for method in get_access_methods_for_codes(codes)]


def _access_gate_text(user_id: int, lang: str) -> str:
    db = users_store.read()
    rec = db.get(str(user_id)) if isinstance(db, dict) else None
    if isinstance(rec, dict):
        if rec.get("access_request_pending"):
            return t(lang, "access.pending")
        if rec.get("access_request_sent_at") and not rec.get("access_granted"):
            return t(lang, "access.rejected")
    return t(lang, "access.welcome")


def _resolve_profile_name(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    db = users_store.read()
    rec = db.get(str(user_id)) if isinstance(db, dict) else None
    if not isinstance(rec, dict):
        return None
    profile_name = str(rec.get("profile_name") or "").strip()
    if profile_name and get_profile(profile_name):
        return profile_name
    candidate = str(rec.get("username") or "").strip().lstrip("@")
    if candidate:
        if get_profile(candidate):
            users_store.upsert_user(user_id, profile_name=candidate)
            return candidate
        candidate_lower = candidate.lower()
        profiles = subs_store.read()
        for name, profile_rec in profiles.items():
            if not isinstance(profile_rec, dict):
                continue
            normalized = str(name).strip()
            if normalized and normalized.lower() == candidate_lower:
                users_store.upsert_user(user_id, profile_name=normalized)
                return normalized
    return None


def start_cmd(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    def mut(db: Dict[str, Any]) -> Dict[str, Any]:
        rec = db.get(str(user.id))
        if not isinstance(rec, dict):
            rec = {}
        profile_name = str(rec.get("profile_name") or "").strip()
        if not profile_name:
            candidate = str(user.username or "").strip().lstrip("@")
            if candidate and get_profile(candidate):
                profile_name = candidate
        rec.update(
            {
                "chat_id": chat.id,
                "username": user.username or "",
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "profile_name": profile_name or rec.get("profile_name"),
                "locale": rec.get("locale") or detect_locale(update),
                "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "notify_access_requests": bool(rec.get("notify_access_requests", True)),
                "telemetry_enabled": bool(rec.get("telemetry_enabled", False)),
                "access_granted": bool(rec.get("access_granted", False)),
                "access_request_pending": bool(rec.get("access_request_pending", False)),
            }
        )
        db[str(user.id)] = rec
        return db

    users_store.update(mut)
    lang = get_locale_for_update(update)
    has_access = _has_access(update)
    if has_access:
        def clear_pending(db: Dict[str, Any]) -> Dict[str, Any]:
            rec = db.get(str(user.id))
            if isinstance(rec, dict):
                rec["access_request_pending"] = False
                db[str(user.id)] = rec
            return db
        users_store.update(clear_pending)
    if not has_access:
        text = _access_gate_text(user.id, lang)
        update.effective_message.reply_text(
            f"*{get_menu_title()}*\n\n{text}",
            parse_mode=PARSE_MODE,
            reply_markup=kb_main_menu(False, False, lang),
            disable_web_page_preview=True,
        )
        return
    update.effective_message.reply_text(
        f"*{get_menu_title()}*\n\n{t(lang, 'menu.choose_action')}",
        parse_mode=PARSE_MODE,
        reply_markup=kb_main_menu(_is_admin(update), has_access, lang),
        disable_web_page_preview=True,
    )


def whoami_cmd(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not user:
        return
    update.effective_message.reply_text(f"Your id: {user.id}\nusername: @{user.username or ''}")


def version_cmd(update: Update, context: CallbackContext) -> None:
    lang = get_locale_for_update(update)
    update.effective_message.reply_text(f"{get_menu_title()}\n{t(lang, 'version.label', version=APP_VERSION)}")


def getkey_cmd(update: Update, context: CallbackContext) -> None:
    start_cmd(update, context)
