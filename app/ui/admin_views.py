from __future__ import annotations

from collections import defaultdict
from typing import List, Optional, Set, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import CB_CFG, LIST_PAGE_SIZE
from domain.servers import get_access_methods, get_access_methods_for_codes, get_server
from i18n import t
from services.provisioning_state import render_profile_server_state_summary


def render_proto_keyboard(selected: Set[str], lang: str = "ru") -> InlineKeyboardMarkup:
    def mark(code: str, label: str) -> str:
        return ("✅ " if code in selected else "⬜ ") + label

    rows: List[List[InlineKeyboardButton]] = []
    current_row: List[InlineKeyboardButton] = []
    for method in get_access_methods():
        current_row.append(
            InlineKeyboardButton(mark(method.code, method.short_label), callback_data=f"{CB_CFG}proto:{method.code}")
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append(
        [
            InlineKeyboardButton("✅ Далее" if lang == "ru" else "✅ Next", callback_data=f"{CB_CFG}proto:done"),
            InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}back"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def render_protocols_summary(protocols: Set[str]) -> str:
    if not protocols:
        return "—"

    grouped = defaultdict(list)
    for method in get_access_methods_for_codes(sorted(protocols)):
        grouped[method.server_key].append(method)

    lines = []
    for server_key, methods in grouped.items():
        server = get_server(server_key)
        labels = ", ".join(method.short_label.split(" ", 1)[1] for method in methods)
        lines.append(f"• {server.flag} *{server.title}*: {labels}")
    return "\n".join(lines)


def render_protocol_select_text(name: str, selected: Set[str], editing: bool = False, lang: str = "ru") -> str:
    summary = render_protocols_summary(selected)
    action = "Измени" if editing and lang == "ru" else "Выбери" if lang == "ru" else "Update" if editing else "Choose"
    profile_label = "Профиль" if lang == "ru" else "Profile"
    choose_text = "серверы и способы подключения" if lang == "ru" else "servers and connection methods"
    current_label = "Текущий выбор" if lang == "ru" else "Current selection"
    done_text = "Когда закончишь, нажми *Далее*." if lang == "ru" else "When finished, press *Next*."
    return (
        f"{profile_label}: *{name}*\n\n"
        f"{action} {choose_text}.\n\n"
        f"{current_label}:\n{summary}\n\n"
        f"{done_text}"
    )


def render_sub_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("7 дней" if lang == "ru" else "7 days", callback_data=f"{CB_CFG}sub:7"),
                InlineKeyboardButton("30 дней" if lang == "ru" else "30 days", callback_data=f"{CB_CFG}sub:30"),
            ],
            [
                InlineKeyboardButton("90 дней" if lang == "ru" else "90 days", callback_data=f"{CB_CFG}sub:90"),
                InlineKeyboardButton("♾ Бессрочная" if lang == "ru" else "♾ Lifetime", callback_data=f"{CB_CFG}sub:inf"),
            ],
            [
                InlineKeyboardButton("✏️ Другое" if lang == "ru" else "✏️ Custom", callback_data=f"{CB_CFG}sub:custom"),
                InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}back"),
            ],
        ]
    )


def render_pick(names: List[str], page: int, lang: str = "ru") -> Tuple[str, InlineKeyboardMarkup]:
    total = len(names)
    pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = names[page * LIST_PAGE_SIZE : (page + 1) * LIST_PAGE_SIZE]

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(f"👤 {name}", callback_data=f"{CB_CFG}pick:{name}")]
        for name in chunk
    ]

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev" if lang == "en" else "⬅️ Prev", callback_data=f"{CB_CFG}pickpage:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data=f"{CB_CFG}pickpage:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{CB_CFG}pickpage:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(t(lang, "admin.wizard.search"), callback_data=f"{CB_CFG}search")])
    rows.append([InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}cancel")])
    return t(lang, "admin.wizard.choose_profile", total=total), InlineKeyboardMarkup(rows)


def render_profile_dashboard(names: List[str], page: int, lang: str = "ru") -> Tuple[str, InlineKeyboardMarkup]:
    total = len(names)
    pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = names[page * LIST_PAGE_SIZE : (page + 1) * LIST_PAGE_SIZE]

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(f"👤 {name}", callback_data=f"{CB_CFG}card:{name}")]
        for name in chunk
    ]

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{CB_CFG}dashboard:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data=f"{CB_CFG}dashboard:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{CB_CFG}dashboard:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(t(lang, "admin.wizard.search"), callback_data=f"{CB_CFG}search")])
    rows.append([InlineKeyboardButton(t(lang, "admin.wizard.new_profile"), callback_data=f"{CB_CFG}start:create")])
    rows.append([InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}cancel")])
    return t(lang, "admin.wizard.profiles", total=total), InlineKeyboardMarkup(rows)


def render_edit_menu(name: str, protocols: Set[str], sub_days: Optional[int], frozen: bool, lang: str = "ru") -> Tuple[str, InlineKeyboardMarkup]:
    proto_txt = render_protocols_summary(protocols)
    state_txt = render_profile_server_state_summary(name, lang)
    sub_txt = "♾ бессрочная" if sub_days is None else f"{sub_days} дн." if lang == "ru" else "♾ lifetime" if sub_days is None else f"{sub_days} d."
    fr = "🧊 заморожен" if frozen and lang == "ru" else "✅ активен" if lang == "ru" else "🧊 frozen" if frozen else "✅ active"
    title = "✏️ *Редактирование:*" if lang == "ru" else "✏️ *Editing:*"
    access = "Доступ" if lang == "ru" else "Access"
    provision = "Применение" if lang == "ru" else "Provisioning"
    sub = "Подписка" if lang == "ru" else "Subscription"
    status = "Статус" if lang == "ru" else "Status"
    choose = "Выбери поле для изменения:" if lang == "ru" else "Choose a field to edit:"
    return (
        (
            f"{title} `{name}`\n\n"
            f"{access}:\n{proto_txt}\n\n"
            f"{provision}:\n{state_txt}\n\n"
            f"{sub}: *{sub_txt}*\n"
            f"{status}: *{fr}*\n\n"
            f"{choose}"
        ),
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔌 Протоколы" if lang == "ru" else "🔌 Protocols", callback_data=f"{CB_CFG}edit:proto")],
                [InlineKeyboardButton("⏳ Подписка" if lang == "ru" else "⏳ Subscription", callback_data=f"{CB_CFG}edit:sub")],
                [InlineKeyboardButton("🧊 Статус" if lang == "ru" else "🧊 Status", callback_data=f"{CB_CFG}edit:status")],
                [
                    InlineKeyboardButton("💾 Сохранить" if lang == "ru" else "💾 Save", callback_data=f"{CB_CFG}edit:save"),
                    InlineKeyboardButton("🗑 Удалить профиль" if lang == "ru" else "🗑 Delete Profile", callback_data=f"{CB_CFG}edit:delete"),
                ],
                [InlineKeyboardButton("⬅️ К профилю" if lang == "ru" else "⬅️ To Profile", callback_data=f"{CB_CFG}card:{name}")],
                [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}cancel")],
            ]
        ),
    )


def render_status_menu(name: str, frozen: bool, lang: str = "ru") -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🧊 *Статус профиля:* `{name}`\n\n"
        f"Сейчас: *{'заморожен' if frozen else 'активен'}*\n\n"
        "Выбери действие:"
        if lang == "ru"
        else
        f"🧊 *Profile status:* `{name}`\n\n"
        f"Current: *{'frozen' if frozen else 'active'}*\n\n"
        "Choose an action:"
    )
    return (
        text,
        InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🧊 Freeze", callback_data=f"{CB_CFG}edit:freeze"),
                    InlineKeyboardButton("🔥 Unfreeze", callback_data=f"{CB_CFG}edit:unfreeze"),
                ],
                [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}back")],
            ]
        ),
    )


def render_delete_confirm(name: str, lang: str = "ru") -> Tuple[str, InlineKeyboardMarkup]:
    title = "🗑 *Удаление профиля:*" if lang == "ru" else "🗑 *Delete Profile:*"
    body = (
        "Это удалит:\n"
        "• запись профиля из БД\n"
        "• Xray-профиль на выбранных серверах\n"
        "• AWG-профиль на выбранных серверах\n\n"
        "*Точно удалить?*"
        if lang == "ru"
        else
        "This will remove:\n"
        "• the profile record from the database\n"
        "• the Xray profile on selected servers\n"
        "• the AWG profile on selected servers\n\n"
        "*Delete it for sure?*"
    )
    return (
        (
            f"{title} `{name}`\n\n{body}"
        ),
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Да, удалить" if lang == "ru" else "✅ Yes, delete", callback_data=f"{CB_CFG}edit:delete_confirm")],
                [InlineKeyboardButton(t(lang, "menu.back"), callback_data=f"{CB_CFG}back")],
            ]
        ),
    )


def render_profile_card(name: str, protocols: Set[str], sub_days: Optional[int], frozen: bool, lang: str = "ru") -> Tuple[str, InlineKeyboardMarkup]:
    proto_txt = render_protocols_summary(protocols)
    state_txt = render_profile_server_state_summary(name, lang)
    sub_txt = "♾ бессрочная" if sub_days is None else f"{sub_days} дн." if lang == "ru" else "♾ lifetime" if sub_days is None else f"{sub_days} d."
    fr = "🧊 заморожен" if frozen and lang == "ru" else "✅ активен" if lang == "ru" else "🧊 frozen" if frozen else "✅ active"
    access = "Доступ" if lang == "ru" else "Access"
    provision = "Применение" if lang == "ru" else "Provisioning"
    sub = "Подписка" if lang == "ru" else "Subscription"
    status = "Статус" if lang == "ru" else "Status"
    actions = "Быстрые действия:" if lang == "ru" else "Quick actions:"
    return (
        (
            f"👤 *{name}*\n\n"
            f"{access}:\n{proto_txt}\n\n"
            f"{provision}:\n{state_txt}\n\n"
            f"{sub}: *{sub_txt}*\n"
            f"{status}: *{fr}*\n\n"
            f"{actions}"
        ),
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ Редактировать" if lang == "ru" else "✏️ Edit", callback_data=f"{CB_CFG}cardedit:{name}")],
                [
                    InlineKeyboardButton("🔄 Сверить" if lang == "ru" else "🔄 Reconcile", callback_data=f"{CB_CFG}cardreconcile:{name}"),
                    InlineKeyboardButton("🧊 Freeze", callback_data=f"{CB_CFG}cardfreeze:{name}"),
                    InlineKeyboardButton("🔥 Unfreeze", callback_data=f"{CB_CFG}cardunfreeze:{name}"),
                ],
                [InlineKeyboardButton("🗑 Удалить" if lang == "ru" else "🗑 Delete", callback_data=f"{CB_CFG}carddelete:{name}")],
                [InlineKeyboardButton("⬅️ К профилям" if lang == "ru" else "⬅️ To Profiles", callback_data=f"{CB_CFG}dashboard:0")],
            ]
        ),
    )
