# app/handlers/user_profile.py
from __future__ import annotations

from typing import List

from telegram import Update
from telegram.ext import CallbackContext

from config import APP_VERSION, MENU_TITLE, PARSE_MODE
from i18n import get_locale_for_update, set_user_locale, t
from services.server_registry import list_servers
from services.awg_profiles import list_awg_server_keys
from services.ssh_keys import render_public_key_guide
from services.subscriptions import get_allowed_protocols, get_profile, get_subscription_status, users_store
from services.xray import get_server_link_status
from ui.user_views import format_server_access
from utils.keyboards import kb_admin_menu, kb_back_to_admin, kb_language_menu, kb_main_menu, kb_profile_minimal, kb_profile_stats
from utils.tg import answer_cb, safe_edit_message

from .user_common import _human_ago, _human_left, _is_admin, _sub_progress


def _render_admin_status(lang: str) -> str:
    servers = list_servers(include_disabled=True)
    profiles_total = len([name for name in users_store.read().keys()]) if isinstance(users_store.read(), dict) else 0
    lines = [
        t(lang, "admin.status.title"),
        "",
        t(lang, "admin.status.version", version=APP_VERSION),
        t(lang, "admin.status.servers", count=len(servers)),
        t(lang, "admin.status.telegram_users", count=profiles_total),
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


def on_menu_callback(update: Update, context: CallbackContext, payload: str) -> None:
    answer_cb(update)
    is_admin = _is_admin(update)
    user = update.effective_user
    lang = get_locale_for_update(update)

    if payload == "main":
        safe_edit_message(
            update,
            context,
            f"*{MENU_TITLE}*\n\n{t(lang, 'menu.choose_action')}",
            reply_markup=kb_main_menu(is_admin, lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "admin" and is_admin:
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
        safe_edit_message(
            update,
            context,
            t(new_lang, "language.changed", label=t(new_lang, f"language.{new_lang}")),
            reply_markup=kb_main_menu(is_admin, new_lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "profile":
        if not user or not user.username:
            safe_edit_message(
                update,
                context,
                f"{t(lang, 'profile.title')}\n\n{t(lang, 'profile.no_username')}",
                reply_markup=kb_profile_minimal(lang),
                parse_mode=PARSE_MODE,
            )
            return

        name = user.username.lstrip("@")
        prof = get_profile(name)
        st = get_subscription_status(name)
        allowed = get_allowed_protocols(name)
        server_access = format_server_access(name, allowed, list_awg_server_keys(name), lang)

        status_line = t(lang, "status.active")
        if st.get("frozen"):
            status_line = t(lang, "status.frozen")
        elif not st.get("active"):
            status_line = t(lang, "status.inactive")

        safe_edit_message(
            update,
            context,
            (
                f"{t(lang, 'profile.title')}\n\n"
                f"{t(lang, 'profile.name', name=name)}\n"
                f"{t(lang, 'profile.status', status=status_line)}\n"
                f"{t(lang, 'profile.access')}\n"
                f"{server_access}\n\n"
            ),
            reply_markup=kb_profile_minimal(lang),
            parse_mode=PARSE_MODE,
        )
        return

    if payload == "profile_stats":
        if not user or not user.username:
            safe_edit_message(
                update,
                context,
                t(lang, "getkey.need_username"),
                reply_markup=kb_profile_minimal(lang),
                parse_mode=PARSE_MODE,
            )
            return

        name = user.username.lstrip("@")
        st = get_subscription_status(name)
        prof = get_profile(name) or {}
        allowed = get_allowed_protocols(name)
        status_line = t(lang, "status.active")
        if st.get("frozen"):
            status_line = t(lang, "status.frozen")
        elif not st.get("active"):
            status_line = t(lang, "status.inactive")

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

        safe_edit_message(
            update,
            context,
            (
                f"{t(lang, 'profile.stats_title')}\n"
                f"👤 `{name}`\n"
                f"{t(lang, 'profile.status', status=status_line)}\n\n"
                f"{t(lang, 'profile.subscription')}\n"
                f"{t(lang, 'profile.remaining', value=left_txt)}\n"
                f"{t(lang, 'profile.expires', value=exp_txt)}\n\n"
                f"{bar_txt}\n"
                f"{t(lang, 'profile.access_section')}\n"
                f"{server_access}\n"
                + f"{t(lang, 'profile.frozen', value=frozen_flag)}\n\n"
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
        reply_markup=kb_main_menu(is_admin, lang),
        parse_mode=PARSE_MODE,
    )
