"""Базовый класс для страниц интерфейса."""

from __future__ import annotations

from tkinter import ttk

from ...core.task import Event


class BaseView(ttk.Frame):
    """Страница приложения.

    Наследники переопределяют :meth:`build` (создание виджетов) и
    :meth:`reload` (обновление данных).  Тяжёлые страницы строятся лениво —
    при первом показе.
    """

    title: str = ""
    subtitle: str = ""

    def __init__(self, master, app) -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.manager = app.manager
        self._built = False

    # ------------------------------------------------------------------
    @property
    def theme(self):
        return self.app.theme

    @property
    def colors(self):
        return self.app.theme.colors

    @property
    def settings(self):
        return self.manager.settings

    # ------------------------------------------------------------------
    def ensure_built(self) -> None:
        if not self._built:
            self._built = True
            self.build()

    def build(self) -> None:  # pragma: no cover - переопределяется
        """Создаёт виджеты страницы (один раз)."""

    def reload(self) -> None:
        """Обновляет данные страницы."""

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self.ensure_built()
        self.reload()

    def on_refresh(self) -> None:
        self.ensure_built()
        self.reload()

    def on_log(self, text: str) -> None:
        """Получает строку журнала лаунчера."""

    def on_event(self, event: Event) -> None:
        """Получает событие фоновой задачи."""

    # ------------------------------------------------------------------
    def run_task(self, label, work, **kwargs) -> bool:
        return self.app.run_task(label, work, **kwargs)

    def log(self, text: str) -> None:
        self.app.log(text)

    def resolve_icons(self, hits) -> None:
        """Заменяет иконки проектов на «сырые» PNG (Tk не читает WebP)."""
        ids = [getattr(hit, "project_id", "") for hit in hits if getattr(hit, "project_id", "")]
        if not ids:
            return
        try:
            mapping = self.manager.modrinth.icon_urls(ids)
        except Exception:  # noqa: BLE001 - иконки не критичны
            return
        for hit in hits:
            url = mapping.get(getattr(hit, "project_id", ""))
            if url:
                hit.icon_url = url

    # ------------------------------------------------------------------
    def page(self, *, padding=(22, 16, 22, 16)) -> ttk.Frame:
        """Контейнер страницы с едиными отступами."""
        frame = ttk.Frame(self, style="TFrame", padding=padding)
        frame.pack(fill="both", expand=True)
        return frame
