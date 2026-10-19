"""Страницы графического интерфейса."""

from __future__ import annotations

from typing import Dict, Type

from .base import BaseView


def build_view(key: str, master, app) -> BaseView:
    """Создаёт страницу по ключу навигации."""
    from .accounts import AccountsView
    from .console import ConsoleView
    from .home import HomeView
    from .instances import InstancesView
    from .modpacks import ModpacksView
    from .mods import ModsView
    from .settings import SettingsView
    from .versions import VersionsView

    mapping: Dict[str, Type[BaseView]] = {
        "home": HomeView,
        "instances": InstancesView,
        "versions": VersionsView,
        "mods": ModsView,
        "modpacks": ModpacksView,
        "accounts": AccountsView,
        "settings": SettingsView,
        "console": ConsoleView,
    }
    view_cls = mapping.get(key)
    if view_cls is None:
        raise KeyError(f"Неизвестная страница: {key}")
    return view_cls(master, app)


__all__ = ["BaseView", "build_view"]
