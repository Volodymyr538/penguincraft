"""Главная страница: быстрый запуск, состояние системы и экземпляры."""

from __future__ import annotations

from tkinter import ttk
from typing import List

from ...core.util import cpu_count, human_size, is_chromebook, total_memory_mb
from ..widgets import Card, ScrollFrame, StatTile, Table, show_info
from .base import BaseView


class HomeView(BaseView):
    title = "Главная"
    subtitle = "Быстрый запуск и состояние лаунчера"

    def build(self) -> None:
        scroll = ScrollFrame(self, self.theme, padding=(22, 16, 22, 18))
        scroll.pack(fill="both", expand=True)
        body = scroll.body
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        self._instances: List = []

        # --- быстрый запуск ----------------------------------------------
        launch = Card(body, self.theme, "Быстрый запуск")
        launch.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 12))
        inner = launch.body
        inner.columnconfigure(1, weight=1)

        ttk.Label(inner, text="Экземпляр", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.instance_var = ttk.Combobox(inner, state="readonly")
        self.instance_var.grid(row=0, column=1, sticky="ew", pady=4, padx=(10, 0))

        ttk.Label(inner, text="Аккаунт", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.account_label = ttk.Label(inner, text="—", style="Card.TLabel")
        self.account_label.grid(row=1, column=1, sticky="w", pady=4, padx=(10, 0))

        buttons = ttk.Frame(inner, style="Card.TFrame")
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.play_button = ttk.Button(buttons, text="Играть", style="Accent.TButton", command=self._play)
        self.play_button.pack(side="left")
        ttk.Button(buttons, text="Установить / обновить", command=self._install_selected).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Аккаунты", style="Ghost.TButton", command=lambda: self.app.select("accounts")).pack(side="left", padx=(8, 0))

        self.launch_hint = ttk.Label(inner, text="", style="CardMuted.TLabel", wraplength=420, justify="left")
        self.launch_hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # --- система -----------------------------------------------------
        system = Card(body, self.theme, "Система")
        system.grid(row=0, column=1, sticky="nsew", pady=(0, 12))
        grid = system.body
        for column in range(2):
            grid.columnconfigure(column, weight=1, uniform="stat")
        self.tile_java = StatTile(grid, self.theme, "Java", "—")
        self.tile_java.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=6)
        self.tile_memory = StatTile(grid, self.theme, "Память ПК", "—")
        self.tile_memory.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=6)
        self.tile_cpu = StatTile(grid, self.theme, "Ядер", "—")
        self.tile_cpu.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=6)
        self.tile_heap = StatTile(grid, self.theme, "Heap для игры", "—")
        self.tile_heap.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=6)

        root_row = ttk.Frame(system.body, style="Card.TFrame")
        root_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(root_row, text="Каталог данных:", style="CardMuted.TLabel").pack(side="left")
        self.path_label = ttk.Label(root_row, text="", style="Card.TLabel")
        self.path_label.pack(side="left", padx=(6, 0))

        # --- экземпляры --------------------------------------------------
        instances = Card(body, self.theme, "Экземпляры")
        instances.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
        self.table = Table(
            instances.body,
            self.theme,
            [
                ("name", "Название", 220),
                ("version", "Версия", 210),
                ("loader", "Загрузчик", 110),
                ("mods", "Модов", 80, "center"),
                ("size", "Размер", 100, "e"),
                ("last", "Последний запуск", 160),
            ],
            height=7,
            on_activate=lambda _iid: self._play(),
        )
        self.table.pack(fill="both", expand=True)
        row = ttk.Frame(instances.body, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Запустить", style="Accent.TButton", command=self._play).pack(side="left")
        ttk.Button(row, text="Открыть каталог", command=self._open_instance_dir).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Все экземпляры", style="Ghost.TButton", command=lambda: self.app.select("instances")).pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._refresh_system()
        self._refresh_instances()
        self._refresh_account()

    def _refresh_system(self) -> None:
        runtimes = self.manager.java_runtimes()
        if runtimes:
            current = self.manager.settings.java_path
            self.tile_java.set_value(f"{len(runtimes)} шт.")
        else:
            self.tile_java.set_value("не найдена")
        self.tile_memory.set_value(f"{total_memory_mb() // 1024} ГБ")
        self.tile_cpu.set_value(str(cpu_count()))
        self.tile_heap.set_value(f"{self.settings.effective_memory_mb} МБ")
        self.path_label.configure(text=str(self.manager.paths.root))

    def _refresh_instances(self) -> None:
        self._instances = self.manager.instances.list()
        names = [instance.display_name for instance in self._instances]
        self.instance_var.configure(values=names)
        if names and not self.instance_var.get():
            self.instance_var.current(0)
        if not names:
            self.instance_var.set("")

        self.table.clear()
        for instance in self._instances:
            mods = len(list(self.manager.instances.mods_dir(instance.name).glob("*.jar")))
            self.table.add(
                instance.name,
                (
                    instance.display_name,
                    instance.version_id or "не установлена",
                    instance.loader,
                    mods,
                    human_size(self.manager.instances.disk_usage(instance.name)),
                    instance.last_played.replace("T", " ") if instance.last_played else "никогда",
                ),
            )
        current = self.settings.last_instance
        if current and current in [item.name for item in self._instances]:
            self.table.tree.selection_set(current)

    def _refresh_account(self) -> None:
        account = self.manager.accounts.active
        if account is None:
            self.account_label.configure(text="не выбран — добавьте на вкладке «Аккаунты»")
        else:
            state = " (сессия истекла)" if account.expired else ""
            self.account_label.configure(text=f"{account.display} · {account.type_title}{state}")
        self.app.on_game_state_changed()
        running = self.manager.game_running
        self.play_button.configure(state=("disabled" if running or not self._instances else "!disabled"))

    # ------------------------------------------------------------------
    def _selected_instance(self):
        name = self.table.selected()
        if name:
            found = self.manager.instances.get(name)
            if found is not None:
                return found
        display = self.instance_var.get()
        for instance in self._instances:
            if instance.display_name == display:
                return instance
        return self._instances[0] if self._instances else None

    def _open_instance_dir(self) -> None:
        instance = self._selected_instance()
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр.")
            return
        from ...core.util import open_in_file_manager

        open_in_file_manager(self.manager.instances.game_dir(instance.name))

    def _install_selected(self) -> None:
        instance = self._selected_instance()
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр на вкладке «Экземпляры».")
            return
        self.run_task(
            f"Установка «{instance.display_name}»",
            lambda reporter: self.manager.install_instance(instance, reporter=reporter),
            on_done=lambda _plan: self.app.log(f"Экземпляр «{instance.display_name}» готов"),
        )

    def _play(self) -> None:
        instance = self._selected_instance()
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр на вкладке «Экземпляры».")
            return
        account = self.manager.accounts.active
        if account is None:
            show_info(self.app, "Добавьте аккаунт на вкладке «Аккаунты».")
            return
        self.settings.last_instance = instance.name
        self.manager.save_settings()
        self.app.select("console")
        self.app.start_game(instance, account)
