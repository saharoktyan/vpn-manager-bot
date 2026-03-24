# app/handlers/user_profile.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.ext import CallbackContext

from config import ADMIN_IDS, APP_VERSION, LIST_PAGE_SIZE, MENU_TITLE, PARSE_MODE
from domain.servers import get_access_methods_for_codes
from i18n import get_locale_for_update, get_user_locale, set_user_locale, t
from services.app_settings import is_global_telemetry_enabled, set_global_telemetry_enabled
from services.server_registry import list_servers
from services.awg_profiles import list_awg_server_keys
from services.ssh_keys import render_public_key_guide
from services.subscriptions import get_allowed_protocols, get_profile, get_subscription_status, subs_store, users_store, utcnow
from services.traffic_usage import get_profile_monthly_usage
from services.xray import get_server_link_status
from ui.user_views import format_server_access
from utils.keyboards import kb_admin_menu, kb_admin_settings_menu, kb_back_to_admin, kb_language_menu, kb_main_menu, kb_profile_minimal, kb_profile_stats, kb_settings_menu
from utils.tg import answer_cb, safe_delete_update_message, safe_edit_by_ids, safe_edit_message

from .user_common import _access_gate_text, _has_access, _human_ago, _human_left, _is_admin, _resolve_profile_name, _sub_progress


def _md(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )


def _render_admin_status(lang: str) -> str:
    servers = list_servers(include_disabled=True)
    subs = subs_store.read()
    users = users_store.read()
    profiles_total = len([name for name in subs.keys() if not str(name).startswith("_")]) if isinstance(subs, dict) else 0
    pending_requests = sum(1 for rec in users.values() if isinstance(rec, dict) and rec.get("access_request_pending"))
    lines = [
        t(lang, "admin.status.title"),
        "",
        t(lang, "admin.status.version", version=APP_VERSION),
        t(lang, "admin.status.servers", count=len(servers)),
        t(lang, "admin.status.profiles_total", count=profiles_total),
        t(lang, "admin.status.requests_pending", count=pending_requests),
        "",
        t(lang, "admin.status.checklist"),
    ]
    if not servers:
        lines.append(t(lang, "admin.status.no_servers"))
    else:
        ready_servers = sum(1 for server in servers if server.bootstrap_state == "bootstrapped")
        lines.append(t(lang, "admin.status.bootstrap_ready", icon="✅" if ready_servers else "⚠️", ready=ready_servers, total=len(servers)))
        xray_ready = 0
        awg_ready = 0
        for server in servers:
            if "xray" in server.protocol_kinds and get_server_link_status(server.key)[0]:
                xray_ready += 1
            if "awg" in server.protocol_kinds and server.bootstrap_state == "bootstrapped":
                awg_ready += 1
        lines.append(t(lang, "admin.status.xray_ready", icon="✅" if xray_ready else "⚠️", count=xray_ready))
        lines.append(t(lang, "admin.status.awg_ready", icon="✅" if awg_ready else "⚠️", count=awg_ready))
    return "\n".join(lines)


def _format_username(value: str, lang: str) -> str:
    username = value.strip()
    if not username:
        return t(lang, "common.none")
    return f"@{username}"


def _format_bytes(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def _request_state_get(context: CallbackContext) -> Optional[Dict[str, Any]]:
    state = context.user_data.get("access_requests")
    return state if isinstance(state, dict) else None


def _request_state_set(context: CallbackContext, state: Dict[str, Any]) -> None:
    context.user_data["access_requests"] = state


def _request_state_clear(context: CallbackContext) -> None:
    context.user_data.pop("access_requests", None)


def _request_capture_message(update: Update, context: CallbackContext) -> None:
    state = _request_state_get(context) or {}
    q = update.callback_query
    if q and q.message:
        state["chat_id"] = q.message.chat_id
        state["message_id"] = q.message.message_id
        _request_state_set(context, state)


def _request_edit(context: CallbackContext, text: str, reply_markup: Any, parse_mode: Optional[str] = PARSE_MODE) -> bool:
    state = _request_state_get(context)
    if not state:
        return False
    chat_id = state.get("chat_id")
    message_id = state.get("message_id")
    if not chat_id or not message_id:
        return False
    safe_edit_by_ids(context.bot, int(chat_id), int(message_id), text, reply_markup=reply_markup, parse_mode=parse_mode)
    return True


def _all_pending_request_ids() -> List[str]:
    users = users_store.read()
    result: List[str] = []
    for user_id, rec in users.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("access_request_pending"):
            result.append(str(user_id))
    return sorted(result, key=lambda value: int(value) if str(value).isdigit() else str(value))


def _request_label(user_id: str, rec: Dict[str, Any]) -> str:
    username = str(rec.get("username") or "").strip()
    if username:
        return f"@{username}"
    full_name = " ".join(part for part in [str(rec.get("first_name") or "").strip(), str(rec.get("last_name") or "").strip()] if part)
    return full_name or f"id:{user_id}"


def _render_requests_dashboard(ids: List[str], page: int, lang: str) -> tuple[str, Any]:
    total = len(ids)
    if total == 0:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return (
            t(lang, "admin.requests.empty"),
            InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(t(lang, "admin.requests.search"), callback_data="menu:admin_requests_search")],
                    [InlineKeyboardButton(t(lang, "menu.back"), callback_data="menu:admin")],
                ]
            ),
        )

    pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = ids[page * LIST_PAGE_SIZE : (page + 1) * LIST_PAGE_SIZE]
    users = users_store.read()
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for user_id in chunk:
        rec = users.get(str(user_id)) if isinstance(users, dict) else None
        if not isinstance(rec, dict):
            continue
        rows.append([InlineKeyboardButton(f"👤 {_request_label(str(user_id), rec)}", callback_data=f"menu:admin_request_card:{user_id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"menu:admin_requests_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data=f"menu:admin_requests_page:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"menu:admin_requests_page:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(t(lang, "admin.requests.search"), callback_data="menu:admin_requests_search")])
    rows.append([InlineKeyboardButton(t(lang, "menu.back"), callback_data="menu:admin")])
    return t(lang, "admin.requests.title", total=total), InlineKeyboardMarkup(rows)


def _render_request_card(user_id: str, lang: str) -> tuple[str, Any]:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    users = users_store.read()
    rec = users.get(str(user_id)) if isinstance(users, dict) else None
    if not isinstance(rec, dict):
        return t(lang, "admin.requests.user_missing"), kb_back_to_admin(lang)

    username = str(rec.get("username") or "").strip()
    username_text = f"@{username}" if username else "—"
    full_name = " ".join(part for part in [str(rec.get("first_name") or "").strip(), str(rec.get("last_name") or "").strip()] if part) or "—"
    requested_at = str(rec.get("access_request_sent_at") or "—")
    if rec.get("access_request_pending"):
        status_text = t(lang, "admin.requests.status_pending")
    elif rec.get("access_granted"):
        status_text = t(lang, "admin.requests.status_approved")
    else:
        status_text = t(lang, "admin.requests.status_rejected")

    text = (
        f"{t(lang, 'admin.requests.card_title')}\n\n"
        f"id: `{_md(user_id)}`\n"
        f"username: {_md(username_text)}\n"
        f"name: {_md(full_name)}\n"
        f"{t(lang, 'admin.requests.requested_at')}: `{_md(requested_at)}`\n"
        f"status: *{status_text}*"
    )
    rows = []
    if rec.get("access_request_pending"):
        rows.append(
            [
                InlineKeyboardButton(t(lang, "admin.requests.approve"), callback_data=f"menu:admin_request_approve:{user_id}"),
                InlineKeyboardButton(t(lang, "admin.requests.reject"), callback_data=f"menu:admin_request_reject:{user_id}"),
            ]
        )
    rows.append([InlineKeyboardButton(t(lang, "admin.requests.to_list"), callback_data="menu:admin_requests")])
    return (text, InlineKeyboardMarkup(rows))


def _open_requests_dashboard(update: Update, context: CallbackContext, lang: str, *, page: int = 0, ids: Optional[List[str]] = None) -> None:
    _request_capture_message(update, context)
    state = _request_state_get(context) or {}
    if ids is None:
        ids = state.get("ids") if isinstance(state.get("ids"), list) else _all_pending_request_ids()
    state.update({"active": True, "step": "dashboard", "page": page, "ids": ids})
    _request_state_set(context, state)
    text, markup = _render_requests_dashboard(ids, page, lang)
    safe_edit_message(update, context, text, reply_markup=markup, parse_mode=PARSE_MODE)


def _set_admin_flag(user_id: int, **fields: Any) -> None:
    users_store.upsert_user(user_id, **fields)


def _ensure_profile_for_request(user_id: int) -> str:
    users = users_store.read()
    rec = users.get(str(user_id)) if isinstance(users, dict) else None
    existing_profile_name = str(rec.get("profile_name") or "").strip() if isinstance(rec, dict) else ""
    if existing_profile_name and get_profile(existing_profile_name):
        return existing_profile_name
    username = str(rec.get("username") or "").strip() if isinstance(rec, dict) else ""
    candidate = username or f"tg_{user_id}"
    if username and get_profile(candidate):
        candidate = f"tg_{user_id}"
    suffix = 1
    while get_profile(candidate):
        candidate = f"tg_{user_id}_{suffix}"
        suffix += 1

    now = utcnow().isoformat(timespec="minutes")

    def mut(db: Dict[str, Any]) -> Dict[str, Any]:
        item = db.get(candidate)
        if not isinstance(item, dict):
            item = {}
        item.setdefault("type", "none")
        item.setdefault("created_at", now)
        item.setdefault("expires_at", None)
        item.setdefault("frozen", False)
        item.setdefault("warned_before_exp", False)
        item.setdefault("protocols", [])
        item["updated_at"] = now
        db[candidate] = item
        return db

    subs_store.update(mut)
    _set_admin_flag(user_id, profile_name=candidate)
    return candidate


def _admin_notify_enabled(user_id: int) -> bool:
    users = users_store.read()
    rec = users.get(str(user_id)) if isinstance(users, dict) else None
    if not isinstance(rec, dict):
        return True
    return bool(rec.get("notify_access_requests", True))


def _user_telemetry_enabled(user_id: int) -> bool:
    users = users_store.read()
    rec = users.get(str(user_id)) if isinstance(users, dict) else None
    if not isinstance(rec, dict):
        return False
    return bool(rec.get("telemetry_enabled", False))


def admin_menu_text_router(update: Update, context: CallbackContext) -> None:
    if not _is_admin(update):
        return
    state = _request_state_get(context)
    if not state or not state.get("active") or state.get("step") != "search":
        return
    lang = get_locale_for_update(update)
    query = (update.effective_message.text or "").strip().lower()
    users = users_store.read()
    matches: List[str] = []
    for user_id, rec in users.items():
        if not isinstance(rec, dict):
            continue
        if not rec.get("access_request_pending"):
            continue
        haystack = " ".join(
            [
                str(user_id),
                str(rec.get("username") or ""),
                str(rec.get("first_name") or ""),
                str(rec.get("last_name") or ""),
            ]
        ).lower()
        if query in haystack:
            matches.append(str(user_id))
    safe_delete_update_message(update, context)
    if not matches:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        state.update({"active": True, "step": "dashboard", "page": 0, "ids": []})
        _request_state_set(context, state)
        _request_edit(
            context,
            t(lang, "admin.requests.search_empty", query=_md(query)),
            InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data="menu:admin_requests")]]),
            parse_mode=PARSE_MODE,
        )
        return
    state.update({"active": True, "step": "dashboard", "page": 0, "ids": matches})
    _request_state_set(context, state)
    text, markup = _render_requests_dashboard(matches, 0, lang)
    _request_edit(context, text, markup, parse_mode=PARSE_MODE)


def on_menu_callback(update: Update, context: CallbackContext, payload: str) -> None:
    answer_cb(update)
    is_admin = _is_admin(update)
    user = update.effective_user
    lang = get_locale_for_update(update)

    if payload == "main":
        has_access = _has_access(update)
        if not has_access:
            text = _access_gate_text(user.id if user else 0, lang)
            safe_edit_message(
                update,
                context,
                f"*{MENU_TITLE}*\n\n{text}",
                reply_markup=kb_main_menu(False, False, lang),
                parse_mode=PARSE_MODE,
            )
            return
        safe_edit_message(
            update,
            context,
            f"*{MENU_TITLE}*\n\n{t(lang, 'menu.choose_action')}",
            reply_markup=kb_main_menu(is_admin, has_access, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "settings":
        telemetry_available = is_global_telemetry_enabled()
        safe_edit_message(
            update,
            context,
            t(lang, "settings.title"),
            reply_markup=kb_settings_menu(_user_telemetry_enabled(user.id if user else 0), telemetry_available, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "admin" and is_admin:
        _request_state_clear(context)
        safe_edit_message(
            update,
            context,
            f"{t(lang, 'admin.menu_title')}\n\n{t(lang, 'menu.admin_choose')}",
            reply_markup=kb_admin_menu(lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "language":
        safe_edit_message(
            update,
            context,
            t(lang, "language.title"),
            reply_markup=kb_language_menu(lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload.startswith("setlang:") and user:
        new_lang = set_user_locale(user.id, payload.split(":", 1)[1])
        telemetry_available = is_global_telemetry_enabled()
        safe_edit_message(
            update,
            context,
            t(new_lang, "language.changed", label=t(new_lang, f"language.{new_lang}")),
            reply_markup=kb_settings_menu(_user_telemetry_enabled(user.id), telemetry_available, new_lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "settings_toggle_telemetry" and user:
        if not is_global_telemetry_enabled():
            safe_edit_message(
                update,
                context,
                t(lang, "settings.title"),
                reply_markup=kb_settings_menu(False, False, lang),
                parse_mode=PARSE_MODE,
            )
            return
        enabled = not _user_telemetry_enabled(user.id)
        _set_admin_flag(user.id, telemetry_enabled=enabled)
        safe_edit_message(
            update,
            context,
            f"{t(lang, 'settings.saved')}\n\n{t(lang, 'settings.title')}",
            reply_markup=kb_settings_menu(enabled, True, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "admin_settings" and is_admin:
        safe_edit_message(
            update,
            context,
            t(lang, "admin.settings.title"),
            reply_markup=kb_admin_settings_menu(
                _admin_notify_enabled(user.id if user else 0),
                is_global_telemetry_enabled(),
                lang,
            ),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "admin_settings_toggle_notify" and is_admin and user:
        enabled = not _admin_notify_enabled(user.id)
        _set_admin_flag(user.id, notify_access_requests=enabled)
        safe_edit_message(
            update,
            context,
            f"{t(lang, 'admin.settings.saved')}\n\n{t(lang, 'admin.settings.title')}",
            reply_markup=kb_admin_settings_menu(enabled, is_global_telemetry_enabled(), lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "admin_settings_toggle_telemetry" and is_admin:
        enabled = set_global_telemetry_enabled(not is_global_telemetry_enabled())
        safe_edit_message(
            update,
            context,
            f"{t(lang, 'admin.settings.saved')}\n\n{t(lang, 'admin.settings.title')}",
            reply_markup=kb_admin_settings_menu(_admin_notify_enabled(user.id if user else 0), enabled, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "admin_requests" and is_admin:
        _open_requests_dashboard(update, context, lang)
        return

    if payload == "admin_requests_search" and is_admin:
        _request_capture_message(update, context)
        state = _request_state_get(context) or {}
        state.update({"active": True, "step": "search"})
        _request_state_set(context, state)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        safe_edit_message(
            update,
            context,
            t(lang, "admin.requests.search_title"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data="menu:admin_requests")]]),
            parse_mode=PARSE_MODE,
        )
        return

    if payload.startswith("admin_requests_page:") and is_admin:
        page = int(payload.split(":", 1)[1])
        state = _request_state_get(context) or {}
        ids = state.get("ids") if isinstance(state.get("ids"), list) else _all_pending_request_ids()
        _open_requests_dashboard(update, context, lang, page=page, ids=ids)
        return

    if payload.startswith("admin_request_card:") and is_admin:
        _request_capture_message(update, context)
        user_id = payload.rsplit(":", 1)[-1]
        state = _request_state_get(context) or {}
        ids = state.get("ids") if isinstance(state.get("ids"), list) else _all_pending_request_ids()
        state.update({"active": True, "step": "card", "ids": ids, "selected_user_id": user_id})
        _request_state_set(context, state)
        text, markup = _render_request_card(user_id, lang)
        safe_edit_message(update, context, text, reply_markup=markup, parse_mode=PARSE_MODE)
        return

    if payload.startswith("admin_request_approve:") and is_admin:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        req_user_id = int(payload.rsplit(":", 1)[-1])
        profile_name = _ensure_profile_for_request(req_user_id)
        _set_admin_flag(req_user_id, access_granted=True, access_request_pending=False, profile_name=profile_name)
        try:
            context.bot.send_message(chat_id=req_user_id, text=t(get_user_locale(req_user_id), "admin.requests.notify_approved"))
        except Exception:
            pass
        text, markup = _render_request_card(str(req_user_id), lang)
        safe_edit_message(
            update,
            context,
            f"{t(lang, 'admin.requests.approved_with_profile')}\n{t(lang, 'admin.requests.profile_created', name=_md(profile_name))}\n\n{text}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(t(lang, "admin.requests.setup_now"), callback_data=f"cfg:quickedit:{profile_name}")],
                    [InlineKeyboardButton(t(lang, "admin.requests.setup_later"), callback_data="menu:admin_requests")],
                ]
            ),
            parse_mode=PARSE_MODE,
        )
        return

    if payload.startswith("admin_request_reject:") and is_admin:
        req_user_id = int(payload.rsplit(":", 1)[-1])
        _set_admin_flag(req_user_id, access_granted=False, access_request_pending=False)
        try:
            context.bot.send_message(chat_id=req_user_id, text=t(get_user_locale(req_user_id), "admin.requests.notify_rejected"))
        except Exception:
            pass
        text, markup = _render_request_card(str(req_user_id), lang)
        safe_edit_message(update, context, f"{t(lang, 'admin.requests.rejected_admin')}\n\n{text}", reply_markup=markup, parse_mode=PARSE_MODE)
        return

    if payload == "request_access" and user:
        db = users_store.read()
        rec = db.get(str(user.id)) if isinstance(db, dict) else None
        if isinstance(rec, dict) and rec.get("access_request_pending"):
            safe_edit_message(
                update,
                context,
                t(lang, "access.pending"),
                reply_markup=kb_main_menu(False, False, lang),
                parse_mode=PARSE_MODE,
            )
            return

        def mut(users_db):
            item = users_db.get(str(user.id)) if isinstance(users_db.get(str(user.id)), dict) else {}
            item["chat_id"] = update.effective_chat.id if update.effective_chat else item.get("chat_id")
            item["username"] = user.username or ""
            item["first_name"] = user.first_name or ""
            item["last_name"] = user.last_name or ""
            item["locale"] = item.get("locale") or lang
            item["access_request_pending"] = True
            item["access_granted"] = False
            item["access_request_sent_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            item["notify_access_requests"] = bool(item.get("notify_access_requests", True))
            item["telemetry_enabled"] = bool(item.get("telemetry_enabled", False))
            users_db[str(user.id)] = item
            return users_db

        users_store.update(mut)

        username_text = f"@{user.username}" if user.username else "—"
        full_name = " ".join(part for part in [(user.first_name or "").strip(), (user.last_name or "").strip()] if part) or "—"
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        for admin_id in ADMIN_IDS:
            if not _admin_notify_enabled(admin_id):
                continue
            try:
                admin_lang = get_user_locale(admin_id)
                context.bot.send_message(
                    chat_id=admin_id,
                    text=t(admin_lang, "admin.requests.notify_new", user_id=_md(user.id), username=_md(username_text), name=_md(full_name)),
                    parse_mode=PARSE_MODE,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(t(admin_lang, "admin.requests.approve"), callback_data=f"menu:admin_request_approve:{user.id}"),
                                InlineKeyboardButton(t(admin_lang, "admin.requests.reject"), callback_data=f"menu:admin_request_reject:{user.id}"),
                            ],
                            [InlineKeyboardButton(t(admin_lang, "menu.requests"), callback_data="menu:admin_requests")],
                        ]
                    ),
                )
            except Exception:
                pass

        safe_edit_message(
            update,
            context,
            t(lang, "access.request_sent"),
            reply_markup=kb_main_menu(False, False, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "profile":
        if not _has_access(update):
            safe_edit_message(update, context, _access_gate_text(user.id if user else 0, lang), reply_markup=kb_main_menu(False, False, lang), parse_mode=PARSE_MODE)
            return
        profile_name = _resolve_profile_name(user.id if user else None)
        if not user or not profile_name:
            safe_edit_message(
                update,
                context,
                f"{t(lang, 'profile.title')}\n\n{t(lang, 'admin.requests.profile_missing')}",
                reply_markup=kb_profile_minimal(lang),
                parse_mode=PARSE_MODE,
            )
            return

        name = profile_name
        prof = get_profile(name)
        st = get_subscription_status(name)
        allowed = get_allowed_protocols(name)
        server_access = format_server_access(name, allowed, list_awg_server_keys(name), lang)
        username_text = _format_username(str(user.username or ""), lang)

        status_line = t(lang, "status.inactive")
        if prof and st.get("frozen"):
            status_line = t(lang, "status.frozen")
        elif prof and st.get("active"):
            status_line = t(lang, "status.active")

        safe_edit_message(
            update,
            context,
            (
                f"{t(lang, 'profile.title')}\n\n"
                f"{t(lang, 'profile.name', name=name)}\n"
                f"{t(lang, 'profile.status', status=status_line)}\n"
                f"{t(lang, 'profile.identity')}\n"
                f"{t(lang, 'profile.telegram_id', value=user.id)}\n"
                f"{t(lang, 'profile.username', value=username_text)}\n\n"
                f"{t(lang, 'profile.access')}\n"
                f"{server_access}\n\n"
            ),
            reply_markup=kb_profile_minimal(lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "profile_stats":
        if not _has_access(update):
            safe_edit_message(update, context, _access_gate_text(user.id if user else 0, lang), reply_markup=kb_main_menu(False, False, lang), parse_mode=PARSE_MODE)
            return
        profile_name = _resolve_profile_name(user.id if user else None)
        if not user or not profile_name:
            safe_edit_message(
                update,
                context,
                t(lang, "admin.requests.profile_missing"),
                reply_markup=kb_profile_minimal(lang),
                parse_mode=PARSE_MODE,
            )
            return

        name = profile_name
        st = get_subscription_status(name)
        prof = get_profile(name) or {}
        allowed = get_allowed_protocols(name)
        methods = get_access_methods_for_codes(allowed)
        status_line = t(lang, "status.inactive")
        if prof and st.get("frozen"):
            status_line = t(lang, "status.frozen")
        elif prof and st.get("active"):
            status_line = t(lang, "status.active")

        expires_at = prof.get("expires_at") if isinstance(prof, dict) else None
        created_at = prof.get("created_at") if isinstance(prof, dict) else None
        left_txt = "♾"
        exp_txt = "♾"
        if expires_at:
            left_txt = _human_left(expires_at)
            exp_txt = f"до `{expires_at}`"

        uuid_val = prof.get("uuid") if isinstance(prof, dict) else None
        frozen_flag = t(lang, "profile.frozen_yes") if st.get("frozen") else t(lang, "profile.frozen_no")

        awg_server_keys = list_awg_server_keys(name)
        server_access = format_server_access(name, allowed, awg_server_keys, lang)
        server_count = len({method.server_key for method in methods})
        xray_count = len([method for method in methods if method.protocol_kind == "xray"])
        awg_count = len([method for method in methods if method.protocol_kind == "awg"])
        bar_txt = ""
        if expires_at and created_at:
            bar, pct = _sub_progress(created_at, expires_at)
            if bar != "—":
                bar_txt = t(lang, "profile.progress", bar=bar, pct=pct) + "\n"

        u_db = users_store.read()
        u_rec = u_db.get(str(user.id)) if isinstance(u_db, dict) else None
        last_key_at = u_rec.get("last_key_at") if isinstance(u_rec, dict) else None
        key_cnt = u_rec.get("key_issued_count") if isinstance(u_rec, dict) else 0
        last_key_txt = _human_ago(last_key_at) if last_key_at else "—"
        username_text = _format_username(str(user.username or ""), lang)
        created_txt = _human_ago(created_at) if created_at else "—"
        traffic_block = ""
        if is_global_telemetry_enabled():
            if _user_telemetry_enabled(user.id):
                awg_usage = get_profile_monthly_usage(name, "awg")
                xray_usage = get_profile_monthly_usage(name, "xray")
                awg_usage_txt = _format_bytes(int(awg_usage["total_bytes"]))
                xray_usage_txt = _format_bytes(int(xray_usage["total_bytes"]))
                telemetry_line = (
                    f"{t(lang, 'profile.awg_traffic', value=awg_usage_txt)}\n"
                    f"{t(lang, 'profile.xray_traffic', value=xray_usage_txt)}"
                )
            else:
                telemetry_line = t(lang, "profile.telemetry_disabled_user")
            traffic_block = f"{t(lang, 'profile.traffic')}\n{telemetry_line}\n\n"

        safe_edit_message(
            update,
            context,
            (
                f"{t(lang, 'profile.stats_title')}\n"
                f"👤 `{name}`\n"
                f"{t(lang, 'profile.status', status=status_line)}\n\n"
                f"{t(lang, 'profile.identity')}\n"
                f"{t(lang, 'profile.telegram_id', value=user.id)}\n"
                f"{t(lang, 'profile.username', value=username_text)}\n"
                f"{t(lang, 'profile.member_since', value=created_txt)}\n\n"
                f"{t(lang, 'profile.subscription')}\n"
                f"{t(lang, 'profile.remaining', value=left_txt)}\n"
                f"{t(lang, 'profile.expires', value=exp_txt)}\n\n"
                f"{bar_txt}\n"
                f"{t(lang, 'profile.coverage')}\n"
                f"{t(lang, 'profile.servers_count', count=server_count)}\n"
                f"{t(lang, 'profile.protocols_count', count=len(methods))}\n"
                f"{t(lang, 'profile.xray_count', count=xray_count)}\n"
                f"{t(lang, 'profile.awg_count', count=awg_count)}\n\n"
                f"{t(lang, 'profile.access_section')}\n"
                f"{server_access}\n"
                + f"{t(lang, 'profile.frozen', value=frozen_flag)}\n\n"
                f"{traffic_block}"
                f"{t(lang, 'profile.activity')}\n"
                f"{t(lang, 'profile.keys_issued', count=key_cnt)}\n"
                f"{t(lang, 'profile.last_key', value=last_key_txt)}\n\n"
            ),
            reply_markup=kb_profile_stats(is_admin, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "sshkey" and is_admin:
        ok, text = render_public_key_guide(lang)
        if not ok:
            safe_edit_message(
                update,
                context,
                t(lang, "ssh.error_setup", error=text[-1500:]),
                reply_markup=kb_back_to_admin(lang),
                parse_mode=None,
            )
            return
        safe_edit_message(
            update,
            context,
            text[:3900],
            reply_markup=kb_back_to_admin(lang),
            parse_mode=None,
        )
        return

    if payload == "admin_status" and is_admin:
        safe_edit_message(
            update,
            context,
            _render_admin_status(lang),
            reply_markup=kb_back_to_admin(lang),
            parse_mode=PARSE_MODE,
        )
        return

    safe_edit_message(
        update,
        context,
        f"*{MENU_TITLE}*\n\n{t(lang, 'menu.choose_action')}",
        reply_markup=kb_main_menu(is_admin, _has_access(update), lang),
        parse_mode=PARSE_MODE,
    )
