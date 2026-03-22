from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from config import CB_SRV, PARSE_MODE
from i18n import get_locale_for_update, t
from services.provisioning_state import reconcile_server_state, render_server_provisioning_summary, summarize_server_provisioning
from services.server_bootstrap import bootstrap_server, probe_server, sync_server_node_env, sync_xray_server_settings
from services.server_registry import RegisteredServer, get_server, list_servers, upsert_server
from services.xray import get_server_link_status
from utils.tg import answer_cb, safe_delete_by_id, safe_delete_update_message, safe_edit_by_ids, safe_edit_message

from .admin_common import guard, kb_back_menu


def _md(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )


def _wizard_get(context: CallbackContext) -> Optional[Dict[str, Any]]:
    w = context.user_data.get("server_wizard")
    return w if isinstance(w, dict) else None


def _wizard_set(context: CallbackContext, w: Dict[str, Any]) -> None:
    context.user_data["server_wizard"] = w


def _wizard_clear(context: CallbackContext) -> None:
    context.user_data.pop("server_wizard", None)


def _wizard_init(sent_message, mode: str) -> Dict[str, Any]:
    return {
        "active": True,
        "mode": mode,
        "step": "menu" if mode == "menu" else "key",
        "chat_id": sent_message.chat_id,
        "message_id": sent_message.message_id,
        "server_key": None,
        "data": {
            "key": "",
            "title": "",
            "flag": "🏳️",
            "region": "",
            "transport": "ssh",
            "target": "",
            "public_host": "",
            "notes": "",
            "protocol_kinds": set(),
        },
        "locale": "ru",
    }


def _wizard_lang(context: CallbackContext) -> str:
    w = _wizard_get(context)
    return str(w.get("locale") or "ru") if w else "ru"


def _wizard_edit(context: CallbackContext, text: str, markup: InlineKeyboardMarkup) -> None:
    w = _wizard_get(context)
    if not w:
        return
    safe_edit_by_ids(context.bot, int(w["chat_id"]), int(w["message_id"]), text, markup, parse_mode=None)


def _wizard_close(context: CallbackContext, text: str | None = None) -> None:
    w = _wizard_get(context)
    if not w:
        return
    deleted = safe_delete_by_id(context.bot, int(w["chat_id"]), int(w["message_id"]))
    if not deleted and text:
        safe_edit_by_ids(
            context.bot,
            int(w["chat_id"]),
            int(w["message_id"]),
            text,
            reply_markup=kb_back_menu(str(w.get("locale") or "ru")),
            parse_mode=None,
        )
    elif not deleted:
        try:
            context.bot.edit_message_reply_markup(chat_id=int(w["chat_id"]), message_id=int(w["message_id"]), reply_markup=None)
        except Exception:
            pass
    _wizard_clear(context)


def _server_menu_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(lang, "admin.wizard.new_server"), callback_data=f"{CB_SRV}start:create")],
            [InlineKeyboardButton(t(lang, "admin.wizard.edit_server"), callback_data=f"{CB_SRV}start:edit")],
            [InlineKeyboardButton(t(lang, "admin.wizard.close"), callback_data=f"{CB_SRV}cancel")],
        ]
    )


def _transport_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("local", callback_data=f"{CB_SRV}transport:local")],
            [InlineKeyboardButton("ssh", callback_data=f"{CB_SRV}transport:ssh")],
            [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")],
            [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")],
        ]
    )


def _protocol_markup(selected: Set[str], lang: str) -> InlineKeyboardMarkup:
    def mark(code: str, label: str) -> str:
        return ("✅ " if code in selected else "⬜ ") + label

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(mark("xray", "Xray"), callback_data=f"{CB_SRV}protocol:xray")],
            [InlineKeyboardButton(mark("awg", "AWG"), callback_data=f"{CB_SRV}protocol:awg")],
            [InlineKeyboardButton("✅ Далее" if lang == "ru" else "✅ Next", callback_data=f"{CB_SRV}protocol:done")],
            [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")],
            [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")],
        ]
    )


def _pick_server_markup(servers: Sequence[RegisteredServer], lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{server.flag} {server.title} ({server.key})", callback_data=f"{CB_SRV}pick:{server.key}")]
        for server in servers
    ]
    rows.append([InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}list")])
    rows.append([InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")])
    return InlineKeyboardMarkup(rows)


def _server_status(server: RegisteredServer, lang: str) -> tuple[str, str]:
    if server.bootstrap_state == "bootstrapped":
        return "✅", t(lang, "admin.wizard.server_status_ready")
    if "failed" in server.bootstrap_state:
        return "⚠️", t(lang, "admin.wizard.server_status_error")
    if server.bootstrap_state in {"edited", "new"}:
        return "🛠", t(lang, "admin.wizard.server_status_bootstrap")
    return "•", server.bootstrap_state or "unknown"


def _xray_status(server: RegisteredServer, lang: str) -> tuple[str, str]:
    if "xray" not in server.protocol_kinds:
        return "—", t(lang, "admin.wizard.server_status_disabled")
    ready, reason = get_server_link_status(server.key)
    if ready:
        return "✅", t(lang, "admin.wizard.server_status_ready")
    if "incomplete" in reason:
        return "⚠️", t(lang, "admin.wizard.server_status_link_incomplete")
    return "⚠️", reason


def _awg_status(server: RegisteredServer, lang: str) -> tuple[str, str]:
    if "awg" not in server.protocol_kinds:
        return "—", t(lang, "admin.wizard.server_status_disabled")
    if server.bootstrap_state == "bootstrapped":
        return "✅", t(lang, "admin.wizard.server_status_awg_runtime")
    if "failed" in server.bootstrap_state:
        return "⚠️", t(lang, "admin.wizard.server_status_awg_failed")
    return "🛠", t(lang, "admin.wizard.server_status_awg_pending")


def _server_dashboard_text(servers: Sequence[RegisteredServer], lang: str) -> str:
    lines = [t(lang, "admin.wizard.server_menu"), ""]
    for server in servers:
        status_icon, status_text = _server_overall_status(server, lang)
        prov_summary = summarize_server_provisioning(server.key)
        total = int(prov_summary["total"])
        prov_suffix = ""
        if total > 0:
            failed = int(prov_summary["by_status"]["failed"])
            attention = int(prov_summary["by_status"]["needs_attention"])
            ready = int(prov_summary["by_status"]["provisioned"])
            if lang == "ru":
                prov_suffix = f" | профили {ready}/{total}"
            else:
                prov_suffix = f" | profiles {ready}/{total}"
            if failed > 0:
                prov_suffix += f" | {'ошибки' if lang == 'ru' else 'failed'} {failed}"
            elif attention > 0:
                prov_suffix += f" | {'внимание' if lang == 'ru' else 'attention'} {attention}"
        lines.append(
            f"\n{server.flag} {server.title} ({server.key})"
            f"\n  {status_icon} {status_text}{prov_suffix}"
        )
    return "\n".join(lines)


def _server_overall_status(server: RegisteredServer, lang: str) -> tuple[str, str]:
    server_icon, server_text = _server_status(server, lang)
    if server_icon == "⚠️":
        return server_icon, server_text
    if server.bootstrap_state != "bootstrapped":
        return server_icon, server_text

    prov = summarize_server_provisioning(server.key)
    if prov["overall"] == "failed":
        return "⚠️", t(lang, "admin.wizard.server_status_attention")
    if prov["overall"] == "needs_attention":
        return "⚠️", t(lang, "admin.wizard.server_status_attention")

    xray_ready, _ = get_server_link_status(server.key) if "xray" in server.protocol_kinds else (True, "ok")
    awg_ready = ("awg" not in server.protocol_kinds) or server.bootstrap_state == "bootstrapped"
    if xray_ready and awg_ready:
        return "✅", t(lang, "admin.wizard.server_status_ready")
    return "⚠️", t(lang, "admin.wizard.server_status_attention")


def _server_dashboard_markup(servers: Sequence[RegisteredServer], lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{server.flag} {server.title}", callback_data=f"{CB_SRV}card:{server.key}")]
        for server in servers
    ]
    rows.append([InlineKeyboardButton(t(lang, "admin.wizard.new_server"), callback_data=f"{CB_SRV}start:create")])
    rows.append([InlineKeyboardButton(t(lang, "admin.wizard.close"), callback_data=f"{CB_SRV}cancel")])
    return InlineKeyboardMarkup(rows)


def _server_card_text(server: RegisteredServer, lang: str) -> str:
    server_icon, server_text = _server_status(server, lang)
    xray_icon, xray_text = _xray_status(server, lang)
    awg_icon, awg_text = _awg_status(server, lang)
    provisioning_text = render_server_provisioning_summary(server.key, lang)
    protocols = ", ".join(server.protocol_kinds) or "—"
    lines = [
        f"🖥 {server.flag} {server.title} ({server.key})",
        "",
        f"infra: {server_icon} {server_text}",
        f"transport: {server.transport}",
        f"ssh_target: {server.ssh_target or '—'}",
        f"public_host: {server.public_host or '—'}",
        f"protocols: {protocols}",
        "",
        f"xray: {xray_icon} {xray_text}",
        f"xray_host: {server.xray_host or '—'}",
        f"xray_sni: {server.xray_sni or '—'}",
        f"xray_sid: {server.xray_sid or '—'}",
        f"xray ports: {server.xray_tcp_port} / {server.xray_xhttp_port}",
        "",
        f"awg: {awg_icon} {awg_text}",
        f"awg_host: {server.awg_public_host or '—'}",
        f"awg_port: {server.awg_port}",
        f"awg_iface: {server.awg_iface}",
        "",
        f"provisioning:\n{provisioning_text}",
    ]
    if server.notes:
        lines.extend(["", f"notes: {server.notes}"])
    return "\n".join(lines)


def _server_card_markup(server_key: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "admin.wizard.probe"), callback_data=f"{CB_SRV}action:probe:{server_key}"),
                InlineKeyboardButton(t(lang, "admin.wizard.bootstrap"), callback_data=f"{CB_SRV}action:bootstrap:{server_key}"),
            ],
            [
                InlineKeyboardButton(t(lang, "admin.wizard.sync_env"), callback_data=f"{CB_SRV}action:syncenv:{server_key}"),
                InlineKeyboardButton(t(lang, "admin.wizard.sync_xray"), callback_data=f"{CB_SRV}action:syncxray:{server_key}"),
            ],
            [InlineKeyboardButton(t(lang, "admin.wizard.reconcile"), callback_data=f"{CB_SRV}action:reconcile:{server_key}")],
            [InlineKeyboardButton(t(lang, "admin.wizard.edit"), callback_data=f"{CB_SRV}edit:{server_key}")],
            [InlineKeyboardButton(t(lang, "admin.wizard.to_servers"), callback_data=f"{CB_SRV}list")],
        ]
    )


def _server_edit_menu_text(data: Dict[str, Any], lang: str) -> str:
    protocols = ", ".join(sorted(data["protocol_kinds"])) or "—"
    target = data["target"] or "—"
    public_host = data["public_host"] or "—"
    notes = data.get("notes") or "—"
    if lang == "ru":
        return (
            f"✏️ Редактирование сервера `{data['key']}`\n\n"
            f"title: `{data['title']}`\n"
            f"flag: `{data['flag']}`\n"
            f"region: `{data['region']}`\n"
            f"transport: `{data['transport']}`\n"
            f"target: `{target}`\n"
            f"public_host: `{public_host}`\n"
            f"protocols: `{protocols}`\n"
            f"notes: `{notes}`\n\n"
            "Выбери поле для изменения или сохрани изменения."
        )
    return (
        f"✏️ Edit server `{data['key']}`\n\n"
        f"title: `{data['title']}`\n"
        f"flag: `{data['flag']}`\n"
        f"region: `{data['region']}`\n"
        f"transport: `{data['transport']}`\n"
        f"target: `{target}`\n"
        f"public_host: `{public_host}`\n"
        f"protocols: `{protocols}`\n"
        f"notes: `{notes}`\n\n"
        "Choose a field to edit or save the changes."
    )


def _server_edit_menu_markup(server_key: str, lang: str) -> InlineKeyboardMarkup:
    title = "Название" if lang == "ru" else "Title"
    flag = "Флаг" if lang == "ru" else "Flag"
    region = "Регион" if lang == "ru" else "Region"
    transport = "Transport"
    target = "Target"
    public_host = "Public host"
    protocols = "Протоколы" if lang == "ru" else "Protocols"
    notes = "Заметки" if lang == "ru" else "Notes"
    save = "💾 Сохранить" if lang == "ru" else "💾 Save"
    back = "⬅️ К серверу" if lang == "ru" else "⬅️ To Server"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(title, callback_data=f"{CB_SRV}editfield:title"), InlineKeyboardButton(flag, callback_data=f"{CB_SRV}editfield:flag")],
            [InlineKeyboardButton(region, callback_data=f"{CB_SRV}editfield:region"), InlineKeyboardButton(transport, callback_data=f"{CB_SRV}editfield:transport")],
            [InlineKeyboardButton(target, callback_data=f"{CB_SRV}editfield:target"), InlineKeyboardButton(public_host, callback_data=f"{CB_SRV}editfield:public_host")],
            [InlineKeyboardButton(protocols, callback_data=f"{CB_SRV}editfield:protocols"), InlineKeyboardButton(notes, callback_data=f"{CB_SRV}editfield:notes")],
            [InlineKeyboardButton(save, callback_data=f"{CB_SRV}editsave")],
            [InlineKeyboardButton(back, callback_data=f"{CB_SRV}card:{server_key}")],
        ]
    )


def _render_server_card(context: CallbackContext, server_key: str) -> None:
    server = get_server(server_key)
    if not server:
        _wizard_edit(context, t(_wizard_lang(context), "admin.wizard.server_not_found"), kb_back_menu(_wizard_lang(context)))
        return
    _wizard_edit(context, _server_card_text(server, _wizard_lang(context)), _server_card_markup(server.key, _wizard_lang(context)))


def _action_result_text(title: str, rc: int, out: str, back_key: str) -> str:
    status = "✅" if rc == 0 else "⚠️"
    body = (out or "").strip() or "Без вывода"
    if len(body) > 2500:
        body = body[-2500:]
    return f"{status} {title}\n\n{body}\n\nOpen server card: {back_key}"


def _summary_text(data: Dict[str, Any], editing: bool = False, lang: str = "ru") -> str:
    protocols = ", ".join(sorted(data["protocol_kinds"])) or "—"
    target = data["target"] or "—"
    public_host = data["public_host"] or "—"
    action = ("Изменение" if editing else "Создание") if lang == "ru" else ("Editing" if editing else "Creating")
    return (
        f"🖥 {action} {'сервера' if lang == 'ru' else 'server'}\n\n"
        f"key: {_md(data['key'])}\n"
        f"title: {_md(data['title'])}\n"
        f"flag: {_md(data['flag'])}\n"
        f"region: {_md(data['region'])}\n"
        f"transport: {_md(data['transport'])}\n"
        f"target: {_md(target)}\n"
        f"public_host: {_md(public_host)}\n"
        f"protocols: {_md(protocols)}\n\n"
        + ("\n\nПодтвердить?" if lang == "ru" else "\n\nConfirm?")
    )


def _summary_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💾 Сохранить" if lang == "ru" else "💾 Save", callback_data=f"{CB_SRV}save")],
            [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")],
            [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")],
        ]
    )


def _keep_current(text: str, current: str) -> str:
    if text == ".":
        return current
    return text or current


def serverwizard_cmd(update: Update, context: CallbackContext) -> None:
    if not guard(update):
        return
    lang = get_locale_for_update(update)
    sent = update.effective_message.reply_text(t(lang, "admin.wizard.server_menu"))
    w = _wizard_init(sent, "menu")
    w["locale"] = lang
    _wizard_set(context, w)
    servers = list_servers(include_disabled=True)
    _wizard_edit(context, _server_dashboard_text(servers, lang), _server_dashboard_markup(servers, lang))
    safe_delete_update_message(update, context)


def server_wizard_text(update: Update, context: CallbackContext) -> None:
    w = _wizard_get(context)
    if not w or not w.get("active"):
        return
    text = (update.effective_message.text or "").strip()
    data = w["data"]
    step = w["step"]
    lang = _wizard_lang(context)

    if step == "key":
        value = text.lower().strip()
        if not value:
            _wizard_edit(context, t(lang, "admin.wizard.server_create_key"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
            return
        data["key"] = value
        w["step"] = "title"
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_create_title"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
        safe_delete_update_message(update, context)
        return

    if step == "title":
        data["title"] = _keep_current(text, data["title"])
        if w["mode"] == "edit" and w.get("edit_single"):
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            safe_delete_update_message(update, context)
            return
        w["step"] = "flag"
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_create_flag", flag=data["flag"]), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
        safe_delete_update_message(update, context)
        return

    if step == "flag":
        data["flag"] = _keep_current(text, data["flag"])
        if w["mode"] == "edit" and w.get("edit_single"):
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            safe_delete_update_message(update, context)
            return
        w["step"] = "region"
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_create_region"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
        safe_delete_update_message(update, context)
        return

    if step == "region":
        data["region"] = _keep_current(text, data["region"])
        if w["mode"] == "edit" and w.get("edit_single"):
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            safe_delete_update_message(update, context)
            return
        w["step"] = "transport"
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_choose_transport"), _transport_markup(lang))
        safe_delete_update_message(update, context)
        return

    if step == "target":
        data["target"] = _keep_current(text, data["target"])
        if w["mode"] == "edit" and w.get("edit_single"):
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            safe_delete_update_message(update, context)
            return
        w["step"] = "public_host"
        _wizard_set(context, w)
        _wizard_edit(
            context,
            t(lang, "admin.wizard.server_enter_public_host"),
            InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]),
        )
        safe_delete_update_message(update, context)
        return

    if step == "public_host":
        data["public_host"] = _keep_current(text, data["public_host"])
        if w["mode"] == "edit" and w.get("edit_single"):
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            safe_delete_update_message(update, context)
            return
        w["step"] = "protocols"
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_choose_protocols"), _protocol_markup(data["protocol_kinds"], lang))
        safe_delete_update_message(update, context)
        return

    if step == "notes":
        data["notes"] = _keep_current(text, data.get("notes", ""))
        if w["mode"] == "edit":
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
        safe_delete_update_message(update, context)
        return


def _load_server_into_data(server: RegisteredServer) -> Dict[str, Any]:
    return {
        "key": server.key,
        "title": server.title,
        "flag": server.flag,
        "region": server.region,
        "transport": server.transport,
        "target": server.ssh_target or "",
        "public_host": server.public_host or "",
        "notes": server.notes or "",
        "protocol_kinds": set(server.protocol_kinds),
    }


def on_server_callback(update: Update, context: CallbackContext, payload: str) -> None:
    answer_cb(update)
    if not guard(update):
        return
    lang = get_locale_for_update(update)

    if payload == "menu":
        sent = update.callback_query.message
        w = _wizard_init(sent, "menu")
        w["locale"] = lang
        _wizard_set(context, w)
        servers = list_servers(include_disabled=True)
        _wizard_edit(context, _server_dashboard_text(servers, lang), _server_dashboard_markup(servers, lang))
        return

    w = _wizard_get(context)
    if payload == "cancel":
        _wizard_close(context)
        return
    if not w:
        safe_edit_message(update, context, t(lang, "admin.wizard.server_inactive"), reply_markup=kb_back_menu(lang), parse_mode=None)
        return

    data = w["data"]
    lang = _wizard_lang(context)

    if payload == "list":
        servers = list_servers(include_disabled=True)
        w["step"] = "menu"
        _wizard_set(context, w)
        _wizard_edit(context, _server_dashboard_text(servers, lang), _server_dashboard_markup(servers, lang))
        return

    if payload.startswith("card:"):
        _render_server_card(context, payload.split(":", 1)[1])
        return

    if payload == "back":
        if w["mode"] == "create":
            if w["step"] == "title":
                w["step"] = "key"
                _wizard_set(context, w)
                _wizard_edit(context, t(lang, "admin.wizard.server_create_key"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "flag":
                w["step"] = "title"
                _wizard_set(context, w)
                _wizard_edit(context, t(lang, "admin.wizard.server_create_title"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "region":
                w["step"] = "flag"
                _wizard_set(context, w)
                _wizard_edit(context, t(lang, "admin.wizard.server_create_flag", flag=data["flag"]), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "transport":
                w["step"] = "region"
                _wizard_set(context, w)
                _wizard_edit(context, t(lang, "admin.wizard.server_create_region"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "target":
                w["step"] = "transport"
                _wizard_set(context, w)
                _wizard_edit(context, t(lang, "admin.wizard.server_choose_transport"), _transport_markup(lang))
                return
            if w["step"] == "public_host":
                if data["transport"] == "local":
                    w["step"] = "transport"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_choose_transport"), _transport_markup(lang))
                else:
                    w["step"] = "target"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_enter_target"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "protocols":
                w["step"] = "public_host"
                _wizard_set(context, w)
                _wizard_edit(context, t(lang, "admin.wizard.server_enter_public_host"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
        else:
            if w["step"] == "pick":
                servers = list_servers(include_disabled=True)
                w["step"] = "menu"
                _wizard_set(context, w)
                _wizard_edit(context, _server_dashboard_text(servers, lang), _server_dashboard_markup(servers, lang))
                return
            if w["step"] == "title":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                elif w.get("server_key"):
                    _render_server_card(context, str(w["server_key"]))
                else:
                    servers = list_servers(include_disabled=True)
                    _wizard_edit(context, _server_dashboard_text(servers, lang), _server_dashboard_markup(servers, lang))
                return
            if w["step"] == "flag":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                else:
                    w["step"] = "title"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_edit", key=data["key"], title=data["title"]), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "region":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                else:
                    w["step"] = "flag"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_create_flag", flag=data["flag"]), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "transport":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                else:
                    w["step"] = "region"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_create_region"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "target":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                else:
                    w["step"] = "transport"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_choose_transport"), _transport_markup(lang))
                return
            if w["step"] == "public_host":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                else:
                    if data["transport"] == "local":
                        w["step"] = "transport"
                        _wizard_set(context, w)
                        _wizard_edit(context, t(lang, "admin.wizard.server_choose_transport"), _transport_markup(lang))
                    else:
                        w["step"] = "target"
                        _wizard_set(context, w)
                        _wizard_edit(context, t(lang, "admin.wizard.server_enter_target"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "protocols":
                if w.get("edit_single"):
                    w["step"] = "edit_menu"
                    _wizard_set(context, w)
                    _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                else:
                    w["step"] = "public_host"
                    _wizard_set(context, w)
                    _wizard_edit(context, t(lang, "admin.wizard.server_enter_public_host"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
                return
            if w["step"] == "notes":
                w["step"] = "edit_menu"
                _wizard_set(context, w)
                _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
                return
        return

    if payload == "start:create":
        w["mode"] = "create"
        w["step"] = "key"
        w["edit_single"] = False
        w["data"] = {
            "key": "",
            "title": "",
            "flag": "🏳️",
            "region": "",
            "transport": "ssh",
            "target": "",
            "public_host": "",
            "notes": "",
            "protocol_kinds": set(),
        }
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_create_key"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
        return

    if payload == "start:edit":
        servers = list_servers(include_disabled=True)
        w["mode"] = "edit"
        w["step"] = "pick"
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.choose_server"), _pick_server_markup(servers, lang))
        return

    if payload.startswith("edit:"):
        server_key = payload.split(":", 1)[1]
        server = get_server(server_key)
        if not server:
            _wizard_edit(context, t(lang, "admin.wizard.server_not_found"), kb_back_menu(lang))
            return
        w["mode"] = "edit"
        w["server_key"] = server_key
        w["data"] = _load_server_into_data(server)
        w["step"] = "edit_menu"
        w["edit_single"] = False
        _wizard_set(context, w)
        _wizard_edit(context, _server_edit_menu_text(w["data"], lang), _server_edit_menu_markup(server_key, lang))
        return

    if payload.startswith("pick:"):
        server_key = payload.split(":", 1)[1]
        server = get_server(server_key)
        if not server:
            _wizard_edit(context, t(lang, "admin.wizard.server_not_found"), kb_back_menu(lang))
            return
        w["server_key"] = server_key
        w["data"] = _load_server_into_data(server)
        w["step"] = "title"
        _wizard_set(context, w)
        _wizard_edit(
            context,
            t(lang, "admin.wizard.server_edit", key=_md(server_key), title=_md(server.title)),
            InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]),
        )
        return

    if payload.startswith("action:"):
        _, action, server_key = payload.split(":", 2)
        if action == "probe":
            rc, out = probe_server(server_key)
            _wizard_edit(context, _action_result_text(t(lang, "admin.wizard.probe"), rc, out, server_key), _server_card_markup(server_key, lang))
            return
        if action == "bootstrap":
            rc, out = bootstrap_server(server_key)
            _wizard_edit(context, _action_result_text(t(lang, "admin.wizard.bootstrap"), rc, out, server_key), _server_card_markup(server_key, lang))
            return
        if action == "syncenv":
            rc, out = sync_server_node_env(server_key)
            _wizard_edit(context, _action_result_text(t(lang, "admin.wizard.sync_env"), rc, out, server_key), _server_card_markup(server_key, lang))
            return
        if action == "syncxray":
            rc, out = sync_xray_server_settings(server_key)
            _wizard_edit(context, _action_result_text(t(lang, "admin.wizard.sync_xray"), rc, out, server_key), _server_card_markup(server_key, lang))
            return
        if action == "reconcile":
            rc, out = reconcile_xray_server_state(server_key)
            _wizard_edit(context, _action_result_text(t(lang, "admin.wizard.reconcile"), rc, out, server_key), _server_card_markup(server_key, lang))
            return

    if payload.startswith("transport:"):
        data["transport"] = payload.split(":", 1)[1]
        if w["mode"] == "edit" and w.get("edit_single"):
            w["step"] = "edit_menu"
            _wizard_set(context, w)
            _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            return
        w["step"] = "target"
        _wizard_set(context, w)
        if data["transport"] == "local":
            data["target"] = ""
            _wizard_edit(
                context,
                t(lang, "admin.wizard.server_enter_public_host_local"),
                InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]),
            )
            w["step"] = "public_host"
            _wizard_set(context, w)
            return
        _wizard_edit(context, t(lang, "admin.wizard.server_enter_target"), InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
        return

    if payload.startswith("protocol:"):
        item = payload.split(":", 1)[1]
        selected = data["protocol_kinds"]
        if item == "done":
            if not selected:
                _wizard_edit(context, t(lang, "admin.wizard.server_protocol_required"), _protocol_markup(selected, lang))
                return
            if w["mode"] == "edit" and w.get("edit_single"):
                w["step"] = "edit_menu"
                _wizard_set(context, w)
                _wizard_edit(context, _server_edit_menu_text(data, lang), _server_edit_menu_markup(data["key"], lang))
            else:
                _wizard_edit(context, _summary_text(data, editing=w["mode"] == "edit", lang=lang), _summary_markup(lang))
            return
        if item in selected:
            selected.remove(item)
        else:
            selected.add(item)
        _wizard_set(context, w)
        _wizard_edit(context, t(lang, "admin.wizard.server_choose_protocols"), _protocol_markup(selected, lang))
        return

    if payload.startswith("editfield:"):
        field = payload.split(":", 1)[1]
        w["mode"] = "edit"
        w["edit_single"] = True
        if field == "transport":
            w["step"] = "transport"
            _wizard_set(context, w)
            _wizard_edit(context, t(lang, "admin.wizard.server_choose_transport"), _transport_markup(lang))
            return
        if field == "protocols":
            w["step"] = "protocols"
            _wizard_set(context, w)
            _wizard_edit(context, t(lang, "admin.wizard.server_choose_protocols"), _protocol_markup(data["protocol_kinds"], lang))
            return
        if field == "notes":
            w["step"] = "notes"
            _wizard_set(context, w)
            prompt = "Введи заметки для сервера или `.` чтобы оставить как есть." if lang == "ru" else "Enter server notes or `.` to keep the current value."
            _wizard_edit(context, prompt, InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
            return
        field_prompts = {
            "title": t(lang, "admin.wizard.server_create_title"),
            "flag": t(lang, "admin.wizard.server_create_flag", flag=data["flag"]),
            "region": t(lang, "admin.wizard.server_create_region"),
            "target": t(lang, "admin.wizard.server_enter_target"),
            "public_host": t(lang, "admin.wizard.server_enter_public_host"),
        }
        if field in field_prompts:
            w["step"] = field
            _wizard_set(context, w)
            _wizard_edit(context, field_prompts[field], InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_SRV}back")], [InlineKeyboardButton(t(lang, "admin.wizard.cancel"), callback_data=f"{CB_SRV}cancel")]]))
            return

    if payload == "editsave":
        server = update_server_fields(
            data["key"],
            title=data["title"],
            flag=data["flag"],
            region=data["region"],
            transport=data["transport"],
            public_host=data["public_host"] or (data["target"].split("@")[-1] if data["target"] else ""),
            ssh_host=data["target"] or None,
            protocol_kinds=sorted(data["protocol_kinds"]),
            notes=data.get("notes") or "",
            bootstrap_state="edited",
        )
        w["data"] = _load_server_into_data(server)
        w["edit_single"] = False
        w["step"] = "edit_menu"
        _wizard_set(context, w)
        saved = "✅ Изменения сохранены." if lang == "ru" else "✅ Changes saved."
        _wizard_edit(context, f"{saved}\n\n{_server_edit_menu_text(w['data'], lang)}", _server_edit_menu_markup(server.key, lang))
        return

    if payload == "save":
        target = data["target"].strip()
        public_host = data["public_host"].strip()
        server = upsert_server(
            key=data["key"],
            title=data["title"],
            flag=data["flag"],
            region=data["region"],
            transport=data["transport"],
            protocol_kinds=sorted(data["protocol_kinds"]),
            public_host=public_host or target.split("@")[-1],
            ssh_host=target or None,
            bootstrap_state="new" if w["mode"] == "create" else "edited",
        )
        _wizard_close(
            context,
            t(lang, "admin.wizard.server_saved", flag=server.flag, title=server.title, key=server.key, transport=server.transport, protocols=", ".join(server.protocol_kinds)),
        )


def serverconfig_cmd(update: Update, context: CallbackContext) -> None:
    if not guard(update):
        return
    lang = get_locale_for_update(update)
    parts = (update.effective_message.text or "").strip().split()
    if len(parts) != 2:
        update.effective_message.reply_text(t(lang, "admin.cmd.usage_serverconfig"))
        return
    server = get_server(parts[1])
    if not server:
        update.effective_message.reply_text(t(lang, "admin.cmd.server_not_found"), reply_markup=kb_back_menu(lang))
        return
    text = (
        _server_card_text(server, lang)
        + f"\n\n{t(lang, 'admin.cmd.field_edit_hint')}\n"
        + f"/setserverfield {server.key} <field> <value>"
    )
    update.effective_message.reply_text(text, parse_mode=None, reply_markup=_server_card_markup(server.key, lang))


def setserverfield_cmd(update: Update, context: CallbackContext) -> None:
    if not guard(update):
        return
    lang = get_locale_for_update(update)
    from services.server_registry import update_server_fields

    parts = (update.effective_message.text or "").strip().split(maxsplit=3)
    if len(parts) != 4:
        update.effective_message.reply_text(t(lang, "admin.cmd.usage_setserverfield"))
        return
    key, field, value = parts[1], parts[2], parts[3]
    int_fields = {"ssh_port", "xray_tcp_port", "xray_xhttp_port", "awg_port"}
    if field in int_fields:
        value_obj: object = int(value)
    elif field == "protocol_kinds":
        value_obj = [item.strip() for item in value.split(",") if item.strip()]
    elif field == "enabled":
        value_obj = value.lower() in {"1", "true", "yes", "on"}
    else:
        value_obj = value
    server = update_server_fields(key, **{field: value_obj})
    update.effective_message.reply_text(
        t(lang, "admin.cmd.field_updated", field=_md(field), value=_md(value), key=_md(server.key)),
        parse_mode=None,
        reply_markup=kb_back_menu(lang),
    )


def syncnodeenv_cmd(update: Update, context: CallbackContext) -> None:
    if not guard(update):
        return
    lang = get_locale_for_update(update)
    parts = (update.effective_message.text or "").strip().split()
    if len(parts) != 2:
        update.effective_message.reply_text(t(lang, "admin.cmd.usage_syncnodeenv"))
        return
    code, out = sync_server_node_env(parts[1])
    if code != 0:
        update.effective_message.reply_text(
            t(lang, "admin.cmd.sync_error", output=out[-1500:]),
            parse_mode=PARSE_MODE,
            reply_markup=kb_back_menu(lang),
        )
        return
    update.effective_message.reply_text(
        t(lang, "admin.cmd.sync_ok", output=out),
        parse_mode=PARSE_MODE,
        reply_markup=kb_back_menu(lang),
    )
