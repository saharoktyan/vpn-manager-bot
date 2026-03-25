from __future__ import annotations

from collections import defaultdict
from typing import List, Sequence

from domain.servers import AccessMethod, get_access_methods_for_codes, get_server
from i18n import t


def format_server_access(name: str, allowed_codes: List[str], awg_server_keys: Sequence[str], lang: str = "ru") -> str:
    grouped = defaultdict(list)
    for method in get_access_methods_for_codes(allowed_codes):
        grouped[method.server_key].append(method)

    awg_server_key_set = set(awg_server_keys)
    if not grouped:
        return t(lang, "common.none")

    lines = []
    for server_key, methods in grouped.items():
        server = get_server(server_key)
        method_labels = []
        for method in methods:
            suffix = ""
            if method.protocol_kind == "awg":
                suffix = t(lang, "ui.server.access_has_config") if method.server_key in awg_server_key_set else ""
            method_labels.append(f"{method.short_label.split(' ', 1)[1]}{suffix}")
        lines.append(f"• {server.flag} *{server.title}*: " + ", ".join(method_labels))
    return "\n".join(lines)


def render_getkey_overview(methods: List[AccessMethod], lang: str = "ru") -> tuple[str, List[tuple[str, str]]]:
    grouped = defaultdict(list)
    for method in methods:
        grouped[method.server_key].append(method)

    server_items: List[tuple[str, str]] = []
    lines: List[str] = []
    for server_key, server_methods in grouped.items():
        server = get_server(server_key)
        labels = ", ".join(method.short_label.split(" ", 1)[1] for method in server_methods)
        server_items.append((server_key, f"{server.flag} {server.title} · {t(lang, 'ui.getkey.methods_count', count=len(server_methods))}"))
        lines.append(f"• {server.flag} *{server.title}*: {labels}")

    text = f"{t(lang, 'getkey.title')}\n\n{t(lang, 'ui.getkey.choose_server')}"
    if lines:
        text += "\n\n" + "\n".join(lines)
    return text, server_items


def render_server_menu(server_key: str, methods: List[AccessMethod], lang: str = "ru") -> tuple[str, List[tuple[str, str]]]:
    server = get_server(server_key)
    items = [(method.getkey_payload, method.short_label.split(" ", 1)[1]) for method in methods]
    text = f"{server.flag} {server.title}\n\n{t(lang, 'ui.server.choose_method')}"
    if methods:
        text += "\n\n" + "\n".join(f"• {method.short_label.split(' ', 1)[1]}" for method in methods)
    return text, items
