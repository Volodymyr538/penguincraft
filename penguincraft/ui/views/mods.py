"""Страница «Моды»: поиск на Modrinth и управление модами экземпляра."""

from __future__ import annotations

import webbrowser
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Dict, List

from ...core.util import human_size, open_in_file_manager
from ...mods import manager as mod_manager
from ...mods.modrinth import format_downloads
from ..widgets import Table, ask_yes_no, show_error, show_info
from .base import BaseView


class ModsView(BaseView):
    title = "Моды"
    subtitle = "Установка модов с Modrinth и работа с папкой mods"

    def build(self) -> None:
        page = self.page()
        self._instances: List = []
        self._hits: Dict[str, object] = {}
        self._mods: Dict[str, object] = {}

        # --- выбор экземпляра --------------------------------------------
        top = ttk.Frame(page, style="TFrame")
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="Экземпляр:", style="Muted.TLabel").pack(side="left")
        self.instance_var = ttk.Combobox(top, state="readonly", width=30)
        self.instance_var.pack(side="left", padx=(8, 12))
        self.instance_var.bind("<<ComboboxSelected>>", lambda _e: self._instance_changed())
        self.instance_info = ttk.Label(top, text="", style="Muted.TLabel")
        self.instance_info.pack(side="left")
        ttk.Button(top, text="Открыть папку mods", style="Ghost.TButton", command=self._open_mods_dir).pack(side="right")

        notebook = ttk.Notebook(page)
        notebook.pack(fill="both", expand=True)

        # --- вкладка поиска ----------------------------------------------
        search_tab = ttk.Frame(notebook, style="TFrame", padding=12)
        notebook.add(search_tab, text="Поиск в Modrinth")

        bar = ttk.Frame(search_tab, style="TFrame")
        bar.pack(fill="x", pady=(0, 10))
        self.query_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.query_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._search())
        ttk.Button(bar, text="Найти", style="Accent.TButton", command=self._search).pack(side="left", padx=(8, 0))
        self.type_combo = ttk.Combobox(
            bar, state="readonly", width=14,
            values=["Моды", "Ресурспаки", "Шейдеры"],
        )
        self.type_combo.current(0)
        self.type_combo.pack(side="left", padx=(8, 0))

        self.results = Table(
            search_tab,
            self.theme,
            [
                ("title", "Название", 240),
                ("downloads", "Загрузок", 100, "e"),
                ("loader", "Загрузчики", 140),
                ("desc", "Описание", 420),
            ],
            height=12,
            icon_width=46,
            on_activate=lambda _i: self._install_selected(),
        )
        self.results.pack(fill="both", expand=True)

        actions = ttk.Frame(search_tab, style="TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Установить выбранный", style="Accent.TButton", command=self._install_selected).pack(side="left")
        ttk.Button(actions, text="Открыть страницу", command=self._open_page).pack(side="left", padx=(8, 0))
        self.search_status = ttk.Label(actions, text="", style="Muted.TLabel")
        self.search_status.pack(side="right")

        # --- вкладка локальных модов -------------------------------------
        local_tab = ttk.Frame(notebook, style="TFrame", padding=12)
        notebook.add(local_tab, text="Установленные моды")

        self.local_table = Table(
            local_tab,
            self.theme,
            [
                ("title", "Название", 240),
                ("version", "Версия", 110),
                ("loader", "Загрузчик", 110),
                ("state", "Состояние", 110),
                ("file", "Файл", 320),
            ],
            height=14,
            on_select=lambda _i: None,
            on_activate=lambda _i: self._toggle_mod(),
        )
        self.local_table.pack(fill="both", expand=True)

        local_actions = ttk.Frame(local_tab, style="TFrame")
        local_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(local_actions, text="Включить / выключить", command=self._toggle_mod).pack(side="left")
        ttk.Button(local_actions, text="Удалить", style="Danger.TButton", command=self._delete_mod).pack(side="left", padx=(8, 0))
        ttk.Button(local_actions, text="Импорт из файла…", command=self._import_files).pack(side="left", padx=(8, 0))
        ttk.Button(local_actions, text="Обновить", style="Ghost.TButton", command=self.reload).pack(side="right")

        self.local_status = ttk.Label(local_tab, text="", style="Muted.TLabel")
        self.local_status.pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------------
    def preselect(self, instance_name: str) -> None:
        self.ensure_built()
        for index, instance in enumerate(self._instances):
            if instance.name == instance_name:
                self.instance_var.current(index)
                self._instance_changed()
                return

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
        self._instance_changed()

    def _current_instance(self):
        display = self.instance_var.get()
        for instance in self._instances:
            if instance.display_name == display:
                return instance
        return self._instances[0] if self._instances else None

    def _instance_changed(self) -> None:
        instance = self._current_instance()
        if instance is None:
            self.instance_info.configure(text="Экземпляры не найдены")
            self.local_table.clear()
            return
        self.instance_info.configure(
            text=f"{instance.loader} · {instance.game_version or instance.version_id or 'версия не выбрана'}"
        )
        self._refresh_local()

    # ------------------------------------------------------------------
    def _refresh_local(self) -> None:
        instance = self._current_instance()
        self.local_table.clear()
        self._mods.clear()
        if instance is None:
            self.local_status.configure(text="")
            return
        mods = self.manager.list_mods(instance)
        total = 0
        for mod in mods:
            total += mod.size
            self._mods[mod.filename] = mod
            self.local_table.add(
                mod.filename,
                (
                    mod.title,
                    mod.version or "—",
                    mod.loader or "—",
                    "включён" if mod.enabled else "выключен",
                    mod.filename,
                ),
            )
        self.local_status.configure(text=f"Всего: {len(mods)} · {human_size(total)}")

    def _open_mods_dir(self) -> None:
        instance = self._current_instance()
        if instance is None:
            return
        open_in_file_manager(self.manager.instances.mods_dir(instance.name))

    # ------------------------------------------------------------------
    def _search(self) -> None:
        instance = self._current_instance()
        loader = instance.loader if instance and instance.loader != "vanilla" else ""
        game_version = (instance.game_version or instance.version_id) if instance else ""
        project_type = {"Моды": "mod", "Ресурспаки": "resourcepack", "Шейдеры": "shader"}.get(
            self.type_combo.get(), "mod"
        )
        query = self.query_var.get().strip()
        self.search_status.configure(text="Поиск…")

        def work(reporter):
            result = self.manager.modrinth.search(
                query,
                project_type=project_type,
                loaders=[loader] if loader else [],
                game_versions=[game_version] if game_version else [],
                limit=40,
            )
            self.resolve_icons(result.hits)
            return result

        def done(result) -> None:
            self._hits.clear()
            self.results.clear()
            for hit in result.hits:
                iid = hit.project_id or hit.slug
                self._hits[iid] = hit
                self.results.add(
                    iid,
                    (hit.title, format_downloads(hit.downloads), ", ".join(hit.loaders), hit.description),
                )
            self.search_status.configure(text=f"Найдено: {result.total}")
            self._load_icons()

        self.run_task("Поиск в Modrinth", work, on_done=done)

    def _load_icons(self) -> None:
        for iid, hit in list(self._hits.items()):
            url = hit.icon_url
            if not url:
                continue
            self.app.load_icon(url, lambda photo, item=iid: self._apply_icon(item, photo))

    def _apply_icon(self, iid: str, photo) -> None:
        if self.results.tree.exists(iid):
            try:
                self.results.tree.item(iid, image=photo)
            except tk.TclError:  # pragma: no cover
                pass

    def _open_page(self) -> None:
        hit = self._hits.get(self.results.selected())
        if hit is None:
            show_info(self.app, "Выберите проект в списке.")
            return
        try:
            webbrowser.open(f"https://modrinth.com/project/{hit.slug or hit.project_id}")
        except Exception:  # noqa: BLE001
            pass

    def _install_selected(self) -> None:
        instance = self._current_instance()
        hit = self._hits.get(self.results.selected())
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр.")
            return
        if hit is None:
            show_info(self.app, "Выберите проект в списке.")
            return

        def work(reporter):
            return self.manager.install_mod_from_modrinth(instance, hit.project_id, reporter=reporter)

        def done(path) -> None:
            self.log(f"Установлен мод: {path.name}")

        self.run_task(f"Установка «{hit.title}»", work, on_done=done)

    # ------------------------------------------------------------------
    def _toggle_mod(self) -> None:
        filename = self.local_table.selected()
        mod = self._mods.get(filename)
        if mod is None:
            return
        try:
            mod_manager.set_enabled(mod, not mod.enabled)
        except Exception as error:  # noqa: BLE001
            show_error(self.app, error)
            return
        self._refresh_local()

    def _delete_mod(self) -> None:
        filename = self.local_table.selected()
        mod = self._mods.get(filename)
        if mod is None:
            return
        if not ask_yes_no(self.app, f"Удалить мод «{mod.title}» ({mod.filename})?", "Удаление"):
            return
        mod_manager.delete_mod(mod)
        self._refresh_local()

    def _import_files(self) -> None:
        instance = self._current_instance()
        if instance is None:
            show_info(self.app, "Сначала создайте экземпляр.")
            return
        paths = filedialog.askopenfilenames(
            parent=self.app,
            title="Выберите файлы модов",
            filetypes=[("Моды Minecraft", "*.jar *.zip *.litemod"), ("Все файлы", "*.*")],
        )
        if not paths:
            return
        copied, errors = mod_manager.import_mods(paths, self.manager.instances.mods_dir(instance.name))
        self._refresh_local()
        if errors:
            show_error(self.app, "Не удалось импортировать:\n" + "\n".join(errors))
        elif copied:
            self.log(f"Импортировано модов: {len(copied)}")
