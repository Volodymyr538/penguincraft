"""Страница «Модпаки»: установка сборок с Modrinth и из файлов .mrpack."""

from __future__ import annotations

import webbrowser
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Dict

from ...core.util import human_size
from ...mods.modrinth import format_downloads
from ...mods.mrpack import extract_mrpack_meta
from ..widgets import Table, ask_yes_no, show_error, show_info
from .base import BaseView


class ModpacksView(BaseView):
    title = "Модпаки"
    subtitle = "Готовые сборки с модами, конфигами и ресурсами"

    def build(self) -> None:
        page = self.page()
        self._hits: Dict[str, object] = {}
        self._offset = 0

        bar = ttk.Frame(page, style="TFrame")
        bar.pack(fill="x", pady=(0, 10))
        self.query_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.query_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._search())
        ttk.Button(bar, text="Найти", style="Accent.TButton", command=self._search).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Установить из файла…", command=self._install_file).pack(side="left", padx=(8, 0))
        self.sort_combo = ttk.Combobox(
            bar, state="readonly", width=16,
            values=["По релевантности", "По загрузкам", "По новизне", "По подпискам"],
        )
        self.sort_combo.current(0)
        self.sort_combo.pack(side="left", padx=(8, 0))
        self.sort_combo.bind("<<ComboboxSelected>>", lambda _e: self._search())

        self.table = Table(
            page,
            self.theme,
            [
                ("title", "Название", 260),
                ("downloads", "Загрузок", 100, "e"),
                ("version", "Версия", 100),
                ("desc", "Описание", 420),
            ],
            height=14,
            icon_width=46,
            on_activate=lambda _i: self._install_selected(),
        )
        self.table.pack(fill="both", expand=True)

        actions = ttk.Frame(page, style="TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Установить выбранный", style="Accent.TButton", command=self._install_selected).pack(side="left")
        ttk.Button(actions, text="Открыть страницу", command=self._open_page).pack(side="left", padx=(8, 0))
        self.more_button = ttk.Button(actions, text="Показать ещё", command=self._search_more)
        self.more_button.pack(side="left", padx=(8, 0))
        self.more_button.state(["disabled"])
        self.status = ttk.Label(actions, text="", style="Muted.TLabel")
        self.status.pack(side="right")

        self.hint = ttk.Label(
            page,
            text="Модпаки CurseForge не поддерживаются напрямую — используйте версию для Modrinth (.mrpack).",
            style="Muted.TLabel",
        )
        self.hint.pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        pass

    def _search(self, *, offset: int = 0, append: bool = False) -> None:
        query = self.query_var.get().strip()
        index = {
            "По релевантности": "relevance",
            "По загрузкам": "downloads",
            "По новизне": "newest",
            "По подпискам": "follows",
        }.get(self.sort_combo.get(), "relevance")
        self.status.configure(text="Поиск…")
        self.more_button.state(["disabled"])

        def work(reporter):
            result = self.manager.modrinth.search(
                query, project_type="modpack", index=index, limit=40, offset=offset
            )
            self.resolve_icons(result.hits)
            return result

        def done(result) -> None:
            if not append:
                self._hits.clear()
                self.table.clear()
            for hit in result.hits:
                iid = hit.project_id or hit.slug
                if not iid:
                    continue
                self._hits[iid] = hit
                self.table.add(
                    iid,
                    (hit.title, format_downloads(hit.downloads), hit.latest_version, hit.description),
                )
            self._offset = result.offset + len(result.hits)
            self.status.configure(text=f"Показано: {self.table.count()} из {result.total}")
            if self.table.count() < result.total and result.hits:
                self.more_button.state(["!disabled"])
            for iid, hit in list(self._hits.items()):
                if hit.icon_url:
                    self.app.load_icon(hit.icon_url, lambda photo, item=iid: self._apply_icon(item, photo))

        self.run_task("Поиск модпаков", work, on_done=done)

    def _search_more(self) -> None:
        if self._offset <= 0:
            return
        self._search(offset=self._offset, append=True)

    def _apply_icon(self, iid: str, photo) -> None:
        if self.table.tree.exists(iid):
            try:
                self.table.tree.item(iid, image=photo)
            except tk.TclError:  # pragma: no cover
                pass

    def _open_page(self) -> None:
        hit = self._hits.get(self.table.selected())
        if hit is None:
            show_info(self.app, "Выберите модпак в списке.")
            return
        try:
            webbrowser.open(f"https://modrinth.com/modpack/{hit.slug or hit.project_id}")
        except Exception:  # noqa: BLE001
            pass

    def _install_selected(self) -> None:
        hit = self._hits.get(self.table.selected())
        if hit is None:
            show_info(self.app, "Выберите модпак в списке.")
            return
        self.run_task(
            f"Установка модпака «{hit.title}»",
            lambda reporter: self.manager.download_modpack(hit.project_id, reporter=reporter),
            on_done=lambda _result: self.app.refresh_all(),
        )

    def _install_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.app,
            title="Выберите модпак",
            filetypes=[("Модпаки Modrinth", "*.mrpack"), ("Архивы", "*.zip"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            meta = extract_mrpack_meta(path)
        except Exception as error:  # noqa: BLE001
            from ...mods.mrpack import detect_modpack_kind

            kind = detect_modpack_kind(path)
            if kind == "curseforge":
                show_error(
                    self.app,
                    "Это модпак CurseForge. Скачайте версию для Modrinth (.mrpack).",
                )
            else:
                show_error(self.app, error)
            return
        summary = (
            f"Модпак: {meta['name']} {meta['version']}\n"
            f"Minecraft: {meta['minecraft']}\n"
            f"Загрузчик: {meta['loader']} {meta['loader_version']}\n"
            f"Файлов: {meta['files']} ({human_size(meta['size'])})\n\nУстановить как новый экземпляр?"
        )
        if not ask_yes_no(self.app, summary, "Установка модпака"):
            return
        self.run_task(
            f"Установка модпака «{meta['name']}»",
            lambda reporter: self.manager.install_modpack_file(path, reporter=reporter),
            on_done=lambda _result: self.app.refresh_all(),
        )
