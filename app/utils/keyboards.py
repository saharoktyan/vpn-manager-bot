# app/utils/keyboards.py
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import CB_GETKEY, CB_MENU, CB_CFG, CB_SRV
from domain.servers import get_access_methods_for_kind
from i18n import t


def kb_main_menu(is_admin: bool, has_access: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    if not has_access:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "menu.request_access"), callback_data=f"{CB_MENU}request_access")]
        ])
    rows = [
        [InlineKeyboardButton(t(lang, "menu.get_key"), callback_data=f"{CB_GETKEY}menu")],
        [InlineKeyboardButton(t(lang, "menu.profile"), callback_data=f"{CB_MENU}profile")],
        [InlineKeyboardButton(t(lang, "menu.settings"), callback_data=f"{CB_MENU}settings")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(t(lang, "menu.admin"), callback_data=f"{CB_MENU}admin")])
    return InlineKeyboardMarkup(rows)


def kb_admin_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "menu.status"), callback_data=f"{CB_MENU}admin_status")],
        [InlineKeyboardButton(t(lang, "menu.profiles"), callback_data=f"{CB_CFG}start:edit")],
        [InlineKeyboardButton(t(lang, "menu.servers"), callback_data=f"{CB_SRV}menu")],
        [InlineKeyboardButton(t(lang, "menu.requests"), callback_data=f"{CB_MENU}admin_requests")],
        [InlineKeyboardButton(t(lang, "menu.ssh_key"), callback_data=f"{CB_MENU}sshkey")],
        [InlineKeyboardButton(t(lang, "menu.admin_settings"), callback_data=f"{CB_MENU}admin_settings")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")],
    ])


def kb_back_to_admin(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}admin")]])


def kb_back_to_main(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")]])


def kb_profile(is_admin: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")]]
    return InlineKeyboardMarkup(rows)


def kb_getkey_protocols(items: Sequence[Tuple[str, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for code, label in items:
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_GETKEY}{code}")])
    rows.append([InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")])
    return InlineKeyboardMarkup(rows)


def kb_getkey_servers(items: Sequence[Tuple[str, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for server_key, label in items:
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_GETKEY}server:{server_key}")])
    rows.append([InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")])
    return InlineKeyboardMarkup(rows)


def kb_getkey_server_methods(server_key: str, items: Sequence[Tuple[str, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for payload, label in items:
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_GETKEY}{payload}")])
    rows.append([InlineKeyboardButton(t(lang, "menu.to_servers"), callback_data=f"{CB_GETKEY}menu")])
    return InlineKeyboardMarkup(rows)


def kb_xray_transport(method_payload: str, back_payload: Optional[str] = None, lang: str = "ru") -> InlineKeyboardMarkup:
    base_payload = f"{CB_GETKEY}xray_transport:{method_payload}:"
    back_target = back_payload or f"{CB_GETKEY}menu"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("xhttp (основной)", callback_data=f"{base_payload}xhttp")],
        [InlineKeyboardButton("tcp (fallback)", callback_data=f"{base_payload}tcp")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=back_target)],
    ])


def kb_xray_key_actions(method_payload: str, transport: str, back_payload: Optional[str] = None, lang: str = "ru") -> InlineKeyboardMarkup:
    back_target = back_payload or f"{CB_GETKEY}menu"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Показать QR", callback_data=f"{CB_GETKEY}xray_qr:{method_payload}:{transport}")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=back_target)],
    ])

def kb_cfg_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("ru", "menu.cancel"), callback_data=f"{CB_CFG}cancel")]
    ])

def kb_cfg_choose_region() -> InlineKeyboardMarkup:
    rows = []
    for method in get_access_methods_for_kind("awg"):
        rows.append([InlineKeyboardButton(method.label, callback_data=f"{CB_CFG}region:{method.region}")])
    rows.append([InlineKeyboardButton(t("ru", "menu.cancel"), callback_data=f"{CB_CFG}cancel")])
    return InlineKeyboardMarkup(rows)

def kb_back_to_getkey_menu(items: Optional[Sequence[Tuple[str, str]]] = None, lang: str = "ru") -> InlineKeyboardMarkup:
    if items:
        return kb_getkey_protocols(items, lang)
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_GETKEY}menu")]])

def kb_awg_key_actions(region: str, back_payload: Optional[str] = None, lang: str = "ru") -> InlineKeyboardMarkup:
    back_target = back_payload or f"{CB_GETKEY}menu"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Показать QR", callback_data=f"{CB_GETKEY}awg_qr:{region}")],
        [InlineKeyboardButton("⬇️ Скачать .conf", callback_data=f"{CB_GETKEY}awg_conf:{region}")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=back_target)],
    ])

def kb_profile_actions(is_admin: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(t(lang, "menu.get_key"), callback_data=f"{CB_GETKEY}menu")],
        [InlineKeyboardButton(t(lang, "menu.refresh"), callback_data=f"{CB_MENU}profile")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")],
    ]
    if is_admin:
        rows.insert(2, [InlineKeyboardButton(t(lang, "menu.edit_profile"), callback_data=f"{CB_CFG}start:edit")])
    return InlineKeyboardMarkup(rows)

def kb_profile_minimal(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "menu.statistics"), callback_data=f"{CB_MENU}profile_stats")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")],
    ])

def kb_profile_stats(is_admin: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "menu.profile"), callback_data=f"{CB_MENU}profile")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")],
    ])


def kb_language_menu(current_locale: str) -> InlineKeyboardMarkup:
    current_locale = "en" if current_locale == "en" else "ru"
    ru_label = f"✅ {t('ru', 'language.ru')}" if current_locale == "ru" else t("ru", "language.ru")
    en_label = f"✅ {t('en', 'language.en')}" if current_locale == "en" else t("en", "language.en")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ru_label, callback_data=f"{CB_MENU}setlang:ru")],
        [InlineKeyboardButton(en_label, callback_data=f"{CB_MENU}setlang:en")],
        [InlineKeyboardButton(t(current_locale, "menu.back"), callback_data=f"{CB_MENU}settings")],
    ])


def kb_settings_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "menu.language"), callback_data=f"{CB_MENU}language")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}main")],
    ])


def kb_admin_settings_menu(notify_enabled: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    label = t(lang, "admin.settings.notifications_on") if notify_enabled else t(lang, "admin.settings.notifications_off")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"{CB_MENU}admin_settings_toggle_notify")],
        [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_MENU}admin")],
    ])
