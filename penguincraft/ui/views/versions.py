"""Страница «Версии»: список версий Minecraft и установка загрузчиков."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import List

from ...core.util import human_size
from ..widgets import Card, Table, ask_yes_no, show_info
from .base import BaseView
from .instances import CreateInstanceDialog, LOADER_CHOICES


class VersionsView(BaseView):
    title = "Версии"
    subtitle = "Версии Minecraft из каталога Mojang и установленные сборки"

    def build(self) -> None:
        page = self.page()
        self._remote: List = []
        self._loading = False

        toolbar = ttk.Frame(page, style="TFrame")
        toolbar.pack(fill="x", pady=(0, 10))

        self.snapshots_var = tk.BooleanVar(value=bool(self.settings.show_snapshots))
        self.old_var = tk.BooleanVar(value=bool(self.settings.show_old_versions))
        ttk.Checkbutton(
            toolbar, text="Снапшоты", variable=self.snapshots_var, command=self._toggle_filters
        ).pack(side="left")
        ttk.Checkbutton(
            toolbar, text="Устаревшие", variable=self.old_var, command=self._toggle_filters
        ).pack(side="left", padx=(12, 0))

        ttk.Label(toolbar, text="Поиск:", style="Muted.TLabel").pack(side="left", padx=(18, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._render_remote())
        ttk.Entry(toolbar, textvariable=self.search_var, width=18).pack(side="left")

        ttk.Button(toolbar, text="Обновить список", style="Ghost.TButton", command=lambda: self._load_remote(True)).pack(side="right")

        split = ttk.Frame(page, style="TFrame")
        split.pack(fill="both", expand=True)

        left = ttk.Frame(split, style="TFrame")
        left.pack(side="left", fill="both", expand=True)
        self.table = Table(
            left,
            self.theme,
            [
                ("id", "Версия", 180),
                ("type", "Тип", 100),
                ("date", "Дата выхода", 120),
                ("state", "Состояние", 140),
            ],
            height=16,
        )
        self.table.pack(fill="both", expand=True)

        self.status_hint = ttk.Label(left, text="", style="Muted.TLabel")
        self.status_hint.pack(anchor="w", pady=(6, 0))

        self.right = Card(split, self.theme, "Установка", padding=14, width=320)
        self.right.pack(side="left", fill="y", padx=(12, 0))
        self.right.pack_propagate(False)
        panel = self.right.body

        self.selection_label = ttk.Label(panel, text="Версия не выбрана", style="Card.TLabel", wraplength=290, justify="left")
        self.selection_label.pack(anchor="w")

        ttk.Separator(panel, orient="horizontal").pack(fill="x", pady=10)

        ttk.Label(panel, text="Загрузчик", style="CardMuted.TLabel").pack(anchor="w")
        self.loader_combo = ttk.Combobox(
            panel, state="readonly", values=[title for _ident, title in LOADER_CHOICES]
        )
        self.loader_combo.current(1)
        self.loader_combo.pack(fill="x", pady=(4, 8))

        ttk.Label(panel, text="Версия загрузчика (пусто = последняя)", style="CardMuted.TLabel").pack(anchor="w")
        self.loader_version_entry = ttk.Entry(panel)
        self.loader_version_entry.pack(fill="x", pady=(4, 12))

        ttk.Button(panel, text="Установить выбранную", style="Accent.TButton", command=self._install_vanilla).pack(fill="x")
        ttk.Button(panel, text="Установить с загрузчиком", command=self._install_loader).pack(fill="x", pady=(8, 0))
        ttk.Button(panel, text="Создать экземпляр…", style="Ghost.TButton", command=self._create_instance).pack(fill="x", pady=(8, 0))

        ttk.Separator(panel, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(panel, text="Установленные версии", style="CardTitle.TLabel").pack(anchor="w")
        self.local_table = Table(
            panel,
            self.theme,
            [("id", "Версия", 190), ("loader", "Загрузчик", 100)],
            height=7,
            on_select=lambda _i: self._update_install_buttons(),
        )
        self.local_table.pack(fill="both", expand=True, pady=(6, 0))
        ttk.Button(panel, text="Удалить версию", style="Danger.TButton", command=self._delete_local).pack(fill="x", pady=(8, 0))

        self._update_install_buttons()

    # ------------------------------------------------------------------
    def preselect(self, version_id: str) -> None:
        if self.table.tree.exists(version_id):
            self.table.tree.selection_set(version_id)
            self.table.tree.see(version_id)
            self._on_select()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._render_local()
        if not self._remote and not self._loading:
            self._load_remote(False)
        else:
            self._render_remote()
        self._on_select()

    def _toggle_filters(self) -> None:
        self.settings.show_snapshots = bool(self.snapshots_var.get())
        self.settings.show_old_versions = bool(self.old_var.get())
        self.manager.save_settings()
        self._remote = []
        self._load_remote(False)

    def _load_remote(self, force: bool) -> None:
        if self._loading:
            return
        self._loading = True
        self.status_hint.configure(text="Загрузка списка версий…")
        snapshots = bool(self.snapshots_var.get())
        old = bool(self.old_var.get())
        manager = self.manager

        def work() -> None:
            try:
                versions = manager.game_versions(snapshots=snapshots, old=old, force=force)
            except Exception as error:  # noqa: BLE001
                self.app.post(lambda: self._load_failed(error))
                return
            self.app.post(lambda: self._load_done(versions))

        threading.Thread(target=work, name="penguincraft-versions", daemon=True).start()

    def _load_done(self, versions) -> None:
        self._loading = False
        self._remote = list(versions)
        self._render_remote()

    def _load_failed(self, error: BaseException) -> None:
        self._loading = False
        self.status_hint.configure(text=f"Не удалось загрузить версии: {error}")

    def _render_remote(self) -> None:
        query = self.search_var.get().strip().lower()
        selected = self.table.selected()
        self.table.clear()
        local = set(self.manager.repository.list_local())
        count = 0
        for info in self._remote:
            if query and query not in info.id.lower():
                continue
            count += 1
            state = "установлена" if info.id in local else ""
            self.table.add(info.id, (info.id, info.type_title, info.release_date, state))
        self.status_hint.configure(text=f"Показано версий: {count} из {len(self._remote)}")
        if selected and self.table.tree.exists(selected):
            self.table.tree.selection_set(selected)

    def _render_local(self) -> None:
        selected = self.local_table.selected()
        self.local_table.clear()
        for item in self.manager.local_version_list():
            self.local_table.add(item.version_id, (item.version_id, item.loader_title))
        if selected and self.local_table.tree.exists(selected):
            self.local_table.tree.selection_set(selected)

    def _on_select(self) -> None:
        version_id = self.table.selected()
        self.selection_label.configure(
            text=f"Выбрана версия: {version_id}" if version_id else "Версия не выбрана"
        )
        self._update_install_buttons()

    def _update_install_buttons(self) -> None:
        pass

    # ------------------------------------------------------------------
    def _install_vanilla(self) -> None:
        version_id = self.table.selected()
        if not version_id:
            show_info(self.app, "Выберите версию в списке.")
            return
        self.run_task(
            f"Установка Minecraft {version_id}",
            lambda reporter: self.manager.install_version_only(version_id, reporter=reporter),
            on_done=lambda result: self._after_install(result),
        )

    def _install_loader(self) -> None:
        version_id = self.table.selected()
        if not version_id:
            show_info(self.app, "Выберите версию в списке.")
            return
        loader_title = self.loader_combo.get()
        loader = next((ident for ident, title in LOADER_CHOICES if title == loader_title), "fabric")
        loader_version = self.loader_version_entry.get().strip()

        def work(reporter):
            return self.manager.install_version_only(
                version_id, loader=loader, loader_version=loader_version, reporter=reporter
            )

        self.run_task(f"Установка {loader_title} для {version_id}", work, on_done=lambda result: self._after_install(result))

    def _after_install(self, version_id: str) -> None:
        self.log(f"Установлена версия: {version_id}")
        self._render_remote()
        self._render_local()

    def _create_instance(self) -> None:
        version_id = self.table.selected()
        dialog = CreateInstanceDialog(self.app, self.manager, initial_version=version_id or "")
        self.wait_window(dialog)
        data = dialog.result
        if not data:
            return
        manager = self.manager

        def work(reporter):
            instance = manager.instances.create(
                data["name"],
                version_id="",
                game_version=data["game_version"],
                loader=data["loader"],
                loader_version=data["loader_version"],
                icon=data["icon"],
            )
            manager.resolve_version_id(instance, reporter)
            if data["install"]:
                manager.install_instance(instance, reporter=reporter)
            return instance

        self.run_task(f"Создание «{data['name']}»", work)

    def _delete_local(self) -> None:
        version_id = self.local_table.selected()
        if not version_id:
            show_info(self.app, "Выберите установленную версию.")
            return
        used_by = [
            instance.display_name
            for instance in self.manager.instances.list()
            if instance.version_id == version_id
        ]
        message = f"Удалить файлы версии «{version_id}»?"
        if used_by:
            message += "\n\nВнимание: версию используют экземпляры: " + ", ".join(used_by)
        if not ask_yes_no(self.app, message, "Удаление версии"):
            return
        from ...core.util import remove_tree

        remove_tree(self.manager.paths.version_dir(version_id))
        self._render_local()
        self._render_remote()
