"""Страница «Экземпляры»: создание, настройка и запуск установок игры."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import List, Optional

from ...core.util import human_size
from ..widgets import Card, Table, ask_string, ask_yes_no, show_error, show_info
from .base import BaseView


LOADER_CHOICES = [("vanilla", "Ванилла"), ("fabric", "Fabric"), ("quilt", "Quilt"),
                  ("forge", "Forge"), ("neoforge", "NeoForge")]


class CreateInstanceDialog(tk.Toplevel):
    """Диалог создания нового экземпляра."""

    def __init__(self, app, manager, initial_version: str = ""):
        super().__init__(app)
        self.app = app
        self.manager = manager
        self.result: Optional[dict] = None
        self.title("Новый экземпляр")
        self.transient(app)
        self.resizable(False, False)
        self.configure(bg=app.theme.colors["panel"])
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover
            pass

        body = ttk.Frame(self, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Название", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        self.name_entry = ttk.Entry(body, width=34)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=6, padx=(10, 0))
        self.name_entry.insert(0, "Новая сборка")

        ttk.Label(body, text="Версия Minecraft", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        self.version_combo = ttk.Combobox(body, width=32)
        self.version_combo.grid(row=1, column=1, sticky="ew", pady=6, padx=(10, 0))
        if initial_version:
            self.version_combo.set(initial_version)

        ttk.Label(body, text="Загрузчик", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        self.loader_combo = ttk.Combobox(
            body, state="readonly", width=32,
            values=[title for _ident, title in LOADER_CHOICES],
        )
        self.loader_combo.current(1)
        self.loader_combo.grid(row=2, column=1, sticky="ew", pady=6, padx=(10, 0))
        self.loader_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_loader_versions())

        ttk.Label(body, text="Версия загрузчика", style="CardMuted.TLabel").grid(row=3, column=0, sticky="w", pady=6)
        self.loader_version_combo = ttk.Combobox(body, width=32, values=["последняя стабильная"])
        self.loader_version_combo.set("последняя стабильная")
        self.loader_version_combo.grid(row=3, column=1, sticky="ew", pady=6, padx=(10, 0))

        ttk.Label(body, text="Значок", style="CardMuted.TLabel").grid(row=4, column=0, sticky="w", pady=6)
        self.icon_entry = ttk.Entry(body, width=10)
        self.icon_entry.insert(0, "🎮")
        self.icon_entry.grid(row=4, column=1, sticky="w", pady=6, padx=(10, 0))

        self.install_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            body, text="Сразу скачать все файлы игры", variable=self.install_var, style="Card.TCheckbutton"
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 4))

        self.hint = ttk.Label(body, text="Загружаю список версий…", style="CardMuted.TLabel")
        self.hint.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Создать", style="Accent.TButton", command=self._create).pack(side="right")

        self.bind("<Return>", lambda _e: self._create())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.name_entry.focus_set()
        self._load_versions()

    # ------------------------------------------------------------------
    def _load_versions(self) -> None:
        def work() -> None:
            try:
                versions = self.manager.game_versions()
                ids = [item.id for item in versions]
            except Exception as error:  # noqa: BLE001
                self.app.post(lambda: self._versions_failed(error))
                return
            self.app.post(lambda: self._versions_loaded(ids))

        threading.Thread(target=work, name="penguincraft-versions", daemon=True).start()

    def _versions_loaded(self, ids: List[str]) -> None:
        if not self.winfo_exists():
            return
        self.version_combo.configure(values=ids)
        if ids and not self.version_combo.get():
            self.version_combo.set(ids[0])
        self.hint.configure(text=f"Доступно версий: {len(ids)}")

    def _versions_failed(self, error: BaseException) -> None:
        if not self.winfo_exists():
            return
        self.hint.configure(text="Не удалось загрузить список — введите версию вручную")

    def _load_loader_versions(self) -> None:
        loader_title = self.loader_combo.get()
        loader_id = next((ident for ident, title in LOADER_CHOICES if title == loader_title), "fabric")
        game_version = self.version_combo.get().strip()
        if loader_id == "vanilla" or not game_version:
            self.loader_version_combo.configure(values=["последняя стабильная"])
            self.loader_version_combo.set("последняя стабильная")
            return
        self.loader_version_combo.configure(values=["последняя стабильная"])
        self.loader_version_combo.set("последняя стабильная")
        self.hint.configure(text=f"Загружаю версии {loader_title}…")

        def work() -> None:
            from ...loaders import get_loader

            try:
                versions = get_loader(loader_id).list_versions(self.manager.paths, self.manager.http, game_version)
                labels = ["последняя стабильная"] + [item.title for item in versions]
                mapping = {"последняя стабильная": ""}
                for item in versions:
                    mapping[item.title] = item.version
            except Exception as error:  # noqa: BLE001
                self.app.post(lambda: self.hint.configure(text="Не удалось получить версии загрузчика"))
                return
            self.app.post(lambda: self._loader_versions_loaded(labels, mapping))

        threading.Thread(target=work, name="penguincraft-loader-versions", daemon=True).start()

    def _loader_versions_loaded(self, labels: List[str], mapping: dict) -> None:
        if not self.winfo_exists():
            return
        self._loader_version_map = mapping
        self.loader_version_combo.configure(values=labels)
        self.loader_version_combo.set(labels[0])
        self.hint.configure(text=f"Доступно версий загрузчика: {len(labels) - 1}")

    # ------------------------------------------------------------------
    def _create(self) -> None:
        name = self.name_entry.get().strip()
        version = self.version_combo.get().strip()
        if not name:
            show_error(self, "Введите название экземпляра.")
            return
        if not version:
            show_error(self, "Выберите версию Minecraft.")
            return
        loader_title = self.loader_combo.get()
        loader = next((ident for ident, title in LOADER_CHOICES if title == loader_title), "vanilla")
        loader_version_label = self.loader_version_combo.get().strip()
        loader_version = ""
        if loader_version_label and loader_version_label != "последняя стабильная":
            loader_version = getattr(self, "_loader_version_map", {}).get(loader_version_label, "")
        self.result = {
            "name": name,
            "game_version": version,
            "loader": loader,
            "loader_version": loader_version,
            "icon": self.icon_entry.get().strip() or "🎮",
            "install": bool(self.install_var.get()),
        }
        self.destroy()


class InstancesView(BaseView):
    title = "Экземпляры"
    subtitle = "Отдельные установки игры со своими модами и настройками"

    def build(self) -> None:
        page = self.page()

        toolbar = ttk.Frame(page, style="TFrame")
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="Создать", style="Accent.TButton", command=self._create).pack(side="left")
        ttk.Button(toolbar, text="Копировать", command=self._clone).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Переименовать", command=self._rename).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Удалить", style="Danger.TButton", command=self._delete).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Экспорт…", command=self._export).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Импорт…", command=self._import).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Обновить", style="Ghost.TButton", command=self.reload).pack(side="right")

        split = ttk.Frame(page, style="TFrame")
        split.pack(fill="both", expand=True)

        left = ttk.Frame(split, style="TFrame")
        left.pack(side="left", fill="both", expand=True)
        self.table = Table(
            left,
            self.theme,
            [
                ("name", "Название", 200),
                ("version", "Версия", 200),
                ("loader", "Загрузчик", 100),
                ("mods", "Модов", 70, "center"),
                ("size", "Размер", 90, "e"),
            ],
            height=14,
            on_select=lambda _iid: self._update_detail(),
        )
        self.table.pack(fill="both", expand=True)

        self.detail = Card(split, self.theme, "Подробности", padding=14, width=300)
        self.detail.pack(side="left", fill="y", padx=(12, 0))
        self.detail.pack_propagate(False)
        self.detail_info = ttk.Label(
            self.detail.body, text="Выберите экземпляр", style="Card.TLabel",
            justify="left", wraplength=270,
        )
        self.detail_info.pack(anchor="w")
        actions = ttk.Frame(self.detail.body, style="Card.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        self.play_button = ttk.Button(actions, text="Играть", style="Accent.TButton", command=self._play)
        self.play_button.pack(fill="x")
        ttk.Button(actions, text="Установить / обновить", command=self._install).pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Моды", command=self._open_mods).pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Проверить обновления", command=self._check_updates).pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Бэкап миров…", command=self._backup_worlds).pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Открыть каталог", style="Ghost.TButton", command=self._open_dir).pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        instances = self.manager.instances.list()
        selected = self.table.selected()
        self.table.clear()
        for instance in instances:
            mods = len(list(self.manager.instances.mods_dir(instance.name).glob("*.jar")))
            self.table.add(
                instance.name,
                (
                    instance.display_name,
                    instance.version_id or instance.game_version or "не выбрана",
                    instance.loader,
                    mods,
                    human_size(self.manager.instances.disk_usage(instance.name)),
                ),
            )
        if selected and self.table.tree.exists(selected):
            self.table.tree.selection_set(selected)
        self._update_detail()

    def _current(self):
        name = self.table.selected()
        return self.manager.instances.get(name) if name else None

    def _update_detail(self) -> None:
        instance = self._current()
        if instance is None:
            self.detail_info.configure(text="Экземпляр не выбран")
            self.play_button.configure(state="disabled")
            return
        mods = len(list(self.manager.instances.mods_dir(instance.name).glob("*.jar")))
        lines = [
            f"{instance.icon} {instance.display_name}",
            "",
            f"Версия: {instance.version_id or instance.game_version or '—'}",
            f"Загрузчик: {instance.loader} {instance.loader_version}".strip(),
            f"Модов: {mods}",
            f"Размер: {human_size(self.manager.instances.disk_usage(instance.name))}",
            f"Создан: {instance.created_at.replace('T', ' ') if instance.created_at else '—'}",
            f"Играл: {instance.last_played.replace('T', ' ') if instance.last_played else 'никогда'}",
        ]
        if instance.notes:
            lines += ["", instance.notes]
        self.detail_info.configure(text="\n".join(lines))
        self.play_button.configure(state=("disabled" if self.manager.game_running else "!disabled"))

    # ------------------------------------------------------------------
    def _create(self) -> None:
        dialog = CreateInstanceDialog(self.app, self.manager)
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
            reporter.status(f"Подготовка версии {data['game_version']}…")
            manager.resolve_version_id(instance, reporter)
            if data["install"]:
                reporter.status("Загрузка файлов игры…")
                manager.install_instance(instance, reporter=reporter)
            return instance

        def done(instance) -> None:
            self.log(f"Создан экземпляр «{instance.display_name}»")

        self.run_task(f"Создание «{data['name']}»", work, on_done=done)

    def _clone(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        name = ask_string(self.app, "Название копии:", initial=f"{instance.display_name} (копия)")
        if not name:
            return
        self.run_task(
            f"Копирование «{instance.display_name}»",
            lambda _r: self.manager.instances.clone(instance.name, name),
        )

    def _rename(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        name = ask_string(self.app, "Новое название:", initial=instance.display_name)
        if not name:
            return
        self.manager.instances.rename(instance.name, name)
        self.reload()
        self.app.refresh_all()

    def _delete(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        if not ask_yes_no(
            self.app,
            f"Удалить экземпляр «{instance.display_name}» вместе с модами, мирами и настройками?",
            "Удаление",
        ):
            return
        self.manager.instances.delete(instance.name)
        self.reload()
        self.app.refresh_all()

    def _install(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        self.run_task(
            f"Установка «{instance.display_name}»",
            lambda reporter: self.manager.install_instance(instance, reporter=reporter),
        )

    # ------------------------------------------------------------------
    def _export(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        target = filedialog.asksaveasfilename(
            parent=self.app,
            title="Куда сохранить архив экземпляра",
            defaultextension=".zip",
            initialfile=f"{instance.name}.zip",
            filetypes=[("Архив", "*.zip"), ("Все файлы", "*.*")],
        )
        if not target:
            return
        from pathlib import Path

        if Path(target).exists() and not ask_yes_no(
            self.app, f"Файл уже существует:\n{target}\n\nПерезаписать?", "Экспорт"
        ):
            return
        self.run_task(
            f"Экспорт «{instance.display_name}»",
            lambda reporter: self.manager.export_instance(instance, target, reporter=reporter),
            on_done=lambda path: self.app.log(f"Экспортировано в {path}"),
        )

    def _import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.app,
            title="Выберите архив экземпляра",
            filetypes=[("Архив", "*.zip"), ("Все файлы", "*.*")],
        )
        if not path:
            return

        def done(instance) -> None:
            self.app.log(f"Импортирован экземпляр «{instance.display_name}»")
            self.reload()
            self.app.refresh_all()

        self.run_task(
            "Импорт экземпляра",
            lambda reporter: self.manager.import_instance(path, reporter=reporter),
            on_done=done,
        )

    def _backup_worlds(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        worlds = self.manager.instance_worlds(instance)
        if not worlds:
            show_info(self.app, "В этом экземпляре нет миров для резервной копии.")
            return
        target = filedialog.asksaveasfilename(
            parent=self.app,
            title="Куда сохранить копию миров",
            defaultextension=".zip",
            initialfile=f"{instance.name}-saves.zip",
            filetypes=[("Архив", "*.zip"), ("Все файлы", "*.*")],
        )
        if not target:
            return
        self.run_task(
            f"Копия миров «{instance.display_name}»",
            lambda reporter: self.manager.backup_worlds(instance, target, reporter=reporter),
            on_done=lambda path: self.app.log(f"Миры сохранены в {path}"),
        )

    def _check_updates(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return

        def work(reporter):
            return (
                self.manager.check_project_updates(instance),
                self.manager.check_modpack_update(instance),
            )

        def done(result) -> None:
            mods, pack = result
            if not mods and pack is None:
                show_info(self.app, "Обновлений нет — всё актуально.")
                return
            lines = []
            if pack is not None:
                record, version = pack
                lines.append(f"Модпак «{record.title}» → {version.version_number or version.name}")
            for record, version in mods:
                title = record.title or record.filename
                lines.append(f"• {title} → {version.version_number or version.name}")
            summary = "Найдены обновления:\n\n" + "\n".join(lines)
            if pack is not None and ask_yes_no(
                self.app, summary + "\n\nОбновить модпак сейчас?", "Обновления"
            ):
                self.run_task(
                    f"Обновление модпака «{instance.display_name}»",
                    lambda reporter: self.manager.update_modpack(instance, reporter=reporter),
                    on_done=lambda _res: self.app.refresh_all(),
                )
                return
            show_info(self.app, summary, "Обновления")

    def _play(self) -> None:
        instance = self._current()
        if instance is None:
            show_info(self.app, "Выберите экземпляр.")
            return
        account = self.manager.accounts.active
        if account is None:
            show_info(self.app, "Добавьте аккаунт на вкладке «Аккаунты».")
            return
        self.app.select("console")
        self.app.start_game(instance, account)

    def _open_mods(self) -> None:
        instance = self._current()
        if instance is None:
            return
        mods_view = self.app._views.get("mods")
        if mods_view is not None:
            mods_view.preselect(instance.name)
        self.app.select("mods")

    def _open_dir(self) -> None:
        instance = self._current()
        if instance is None:
            return
        from ...core.util import open_in_file_manager

        open_in_file_manager(self.manager.instances.game_dir(instance.name))
