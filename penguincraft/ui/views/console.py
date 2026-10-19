"""Страница «Консоль»: живой вывод игры и команда запуска."""

from __future__ import annotations

from tkinter import ttk
from typing import List

from ..widgets import LogView, show_info
from .base import BaseView


class ConsoleView(BaseView):
    title = "Консоль"
    subtitle = "Вывод игры, журнал лаунчера и команда запуска"

    def build(self) -> None:
        page = self.page()
        top = ttk.Frame(page, style="TFrame")
        top.pack(fill="x", pady=(0, 10))

        ttk.Label(top, text="Экземпляр:", style="Muted.TLabel").pack(side="left")
        self.instance_var = ttk.Combobox(top, state="readonly", width=30)
        self.instance_var.pack(side="left", padx=(8, 16))

        self.start_button = ttk.Button(top, text="Запустить", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(top, text="Остановить", style="Danger.TButton", command=self.app.stop_game)
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Показать команду", style="Ghost.TButton", command=self._show_command).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Очистить", style="Ghost.TButton", command=self._clear).pack(side="right")

        self.logview = LogView(page, self.theme)
        self.logview.pack(fill="both", expand=True)

        self._instances: List = []

    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._instances = self.manager.instances.list()
        names = [instance.display_name for instance in self._instances]
        self.instance_var.configure(values=names)
        if names and not self.instance_var.get():
            current = self.settings.last_instance
            index = next((i for i, item in enumerate(self._instances) if item.name == current), 0)
            self.instance_var.current(index)
        if not names:
            self.instance_var.set("")
        self._update_buttons()
        # Полная пересборка вывода из буфера лаунчера.
        self.logview.clear()
        self.logview.append_many(self.app.log_lines)

    def on_log(self, text: str) -> None:
        if self._built and self.app.current_view is self:
            self.logview.append(text)

    def _update_buttons(self) -> None:
        running = self.manager.game_running
        self.start_button.configure(state=("disabled" if running or not self._instances else "!disabled"))
        self.stop_button.configure(state=("!disabled" if running else "disabled"))
        self.app.on_game_state_changed()

    # ------------------------------------------------------------------
    def _selected_instance(self):
        display = self.instance_var.get()
        for instance in self._instances:
            if instance.display_name == display:
                return instance
        return self._instances[0] if self._instances else None

    def _start(self) -> None:
        instance = self._selected_instance()
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр на вкладке «Экземпляры».")
            return
        account = self.manager.accounts.active
        if account is None:
            show_info(self.app, "Добавьте аккаунт на вкладке «Аккаунты».")
            return
        self.app.start_game(instance, account)

    def _clear(self) -> None:
        self.logview.clear()

    def _show_command(self) -> None:
        instance = self._selected_instance()
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр.")
            return

        def work(reporter):
            return self.manager.prepare_command(instance)

        def done(command) -> None:
            self.logview.append("")
            self.logview.append("Команда запуска:")
            self.logview.append(command.pretty)
            self.logview.append("")

        self.run_task("Подготовка команды", work, on_done=done)
