"""Графический интерфейс PenguinCraft на tkinter.

Пакет подключается лениво: ``cli.cmd_gui`` вызывает :func:`run_app`,
поэтому импорт tkinter происходит только тогда, когда интерфейс реально
нужен (на сервере без дисплея консольные команды продолжают работать).
"""

from __future__ import annotations

__all__ = ["run_app"]


def run_app(manager, *, root=None):
    """Запускает графический интерфейс.  Возвращает код выхода."""
    from .app import LauncherApp

    app = LauncherApp(manager)
    app.mainloop()
    return 0
