"""Страница «Хранилище»: что занимает место и что можно удалить."""

from __future__ import annotations

import threading
from tkinter import ttk
from typing import Dict, Optional

from ...core import storage as storage_module
from ...core.util import human_size, open_in_file_manager
from ..widgets import Card, ScrollFrame, StatTile, Table, ask_yes_no, show_info
from .base import BaseView


class StorageView(BaseView):
    title = "Хранилище"
    subtitle = "Сколько места занимают версии, моды, миры и кэш"

    def build(self) -> None:
        self._report: Optional[storage_module.StorageReport] = None
        self._scanning = False
        self._entries: Dict[str, storage_module.StorageEntry] = {}

        scroll = ScrollFrame(self, self.theme, padding=(22, 16, 22, 18))
        scroll.pack(fill="both", expand=True)
        body = scroll.body
        body.columnconfigure(0, weight=1)

        # --- сводка ------------------------------------------------------
        stats = ttk.Frame(body, style="TFrame")
        stats.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for index in range(4):
            stats.columnconfigure(index, weight=1)
        self._total_tile = StatTile(stats, self.theme, "Лаунчер занимает")
        self._total_tile.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._free_tile = StatTile(stats, self.theme, "Свободно на диске")
        self._free_tile.grid(row=0, column=1, sticky="ew", padx=8)
        self._versions_tile = StatTile(stats, self.theme, "Лишних версий")
        self._versions_tile.grid(row=0, column=2, sticky="ew", padx=8)
        self._runtimes_tile = StatTile(stats, self.theme, "Лишних Java")
        self._runtimes_tile.grid(row=0, column=3, sticky="ew", padx=(8, 0))

        # --- диск --------------------------------------------------------
        disk = Card(body, self.theme, "Диск")
        disk.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self._disk_label = ttk.Label(
            disk.body, text="Считаю…", style="CardMuted.TLabel"
        )
        self._disk_label.pack(anchor="w")
        self._disk_bar = ttk.Progressbar(disk.body, mode="determinate", maximum=100)
        self._disk_bar.pack(fill="x", pady=(8, 0))
        self._disk_hint = ttk.Label(disk.body, text="", style="CardMuted.TLabel")
        self._disk_hint.pack(anchor="w", pady=(6, 0))

        # --- категории ---------------------------------------------------
        categories = Card(body, self.theme, "Категории")
        categories.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        self._table = Table(
            categories.body,
            self.theme,
            [
                ("title", "Категория", 210),
                ("size", "Размер", 100, "e"),
                ("files", "Файлов", 70, "e"),
                ("note", "Что это", 240),
            ],
            height=11,
            on_activate=self._open_selected,
        )
        self._table.pack(fill="both", expand=True)

        # --- экземпляры --------------------------------------------------
        instances = Card(body, self.theme, "Экземпляры")
        instances.grid(row=3, column=0, sticky="nsew", pady=(0, 12))
        self._instances = Table(
            instances.body,
            self.theme,
            [
                ("title", "Экземпляр", 260),
                ("size", "Размер", 120, "e"),
                ("files", "Файлов", 90, "e"),
            ],
            height=6,
            on_activate=self._open_selected,
        )
        self._instances.pack(fill="both", expand=True)
        ttk.Label(
            instances.body,
            text="Двойной щелчок открывает каталог категории или экземпляра.",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        # --- действия ----------------------------------------------------
        actions = Card(body, self.theme, "Очистка")
        actions.grid(row=4, column=0, sticky="ew")
        top_row = ttk.Frame(actions.body, style="Card.TFrame")
        top_row.pack(fill="x")
        ttk.Button(
            top_row, text="Пересчитать", command=self._scan_async
        ).pack(side="left")
        ttk.Button(
            top_row, text="Открыть каталог данных",
            command=lambda: open_in_file_manager(self.manager.paths.root),
        ).pack(side="left", padx=(8, 0))

        clean_row = ttk.Frame(actions.body, style="Card.TFrame")
        clean_row.pack(fill="x", pady=(8, 0))
        ttk.Button(
            clean_row, text="Кэш загрузок", command=self._clean_cache
        ).pack(side="left")
        ttk.Button(
            clean_row, text="Журналы", command=self._clean_logs
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            clean_row, text="Неиспользуемые версии", command=self._clean_versions
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            clean_row, text="Лишняя Java", command=self._clean_runtimes
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            clean_row, text="Копии удалённых", command=self._clean_backups
        ).pack(side="left", padx=(8, 0))
        self._status = ttk.Label(actions.body, text="", style="CardMuted.TLabel")
        self._status.pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._report is None:
            self._scan_async()
        else:
            self._render(self._report)

    # ------------------------------------------------------------------
    def _scan_async(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._status.configure(text="Считаю размеры…")

        def work() -> None:
            try:
                report = self.manager.storage_report()
            except Exception as error:  # noqa: BLE001
                self.app.post(self._scan_failed, error)
                return
            self.app.post(self._scan_done, report)

        threading.Thread(
            target=work, name="penguincraft-storage", daemon=True
        ).start()

    def _scan_done(self, report: storage_module.StorageReport) -> None:
        self._scanning = False
        self._report = report
        self._render(report)
        self._status.configure(text="Готово.")

    def _scan_failed(self, error: BaseException) -> None:
        self._scanning = False
        self._status.configure(text=f"Не удалось посчитать размеры: {error}")

    # ------------------------------------------------------------------
    def _render(self, report: storage_module.StorageReport) -> None:
        self._total_tile.set_value(human_size(report.total))
        self._free_tile.set_value(human_size(report.free))
        self._versions_tile.set_value(str(len(report.unused_versions)))
        self._runtimes_tile.set_value(str(len(report.unused_runtimes)))

        if report.disk_total:
            used = report.disk_used
            percent = used / report.disk_total * 100
            self._disk_bar.configure(maximum=report.disk_total, value=used)
            self._disk_label.configure(
                text=(
                    f"Занято {human_size(used)} из {human_size(report.disk_total)} "
                    f"({percent:.0f} %), свободно {human_size(report.free)}."
                )
            )
            if report.free < 2 * 1024 ** 3:
                self._disk_hint.configure(
                    text="Свободного места меньше 2 ГБ — установка новых версий "
                    "и модпаков может не поместиться.",
                    style="CardDanger.TLabel",
                )
            else:
                self._disk_hint.configure(text="", style="CardMuted.TLabel")
        else:
            self._disk_label.configure(text="Не удалось определить размер диска.")
            self._disk_hint.configure(text="")

        self._table.clear()
        self._entries.clear()
        for index, entry in enumerate(report.entries):
            self._entries[entry.key] = entry
            tags = ("alt",) if index % 2 else ()
            self._table.add(
                entry.key,
                (entry.title, human_size(entry.size), entry.files, entry.note),
                tags=tags,
            )

        self._instances.clear()
        for index, entry in enumerate(report.instances):
            self._entries[entry.key] = entry
            tags = ("alt",) if index % 2 else ()
            self._instances.add(
                entry.key,
                (entry.title, human_size(entry.size), entry.files),
                tags=tags,
            )

    # ------------------------------------------------------------------
    def _open_selected(self, iid: str) -> None:
        entry = self._entries.get(iid)
        if entry is not None:
            open_in_file_manager(entry.path)

    # ------------------------------------------------------------------
    def _clean_cache(self) -> None:
        if not ask_yes_no(
            self,
            "Очистить кэш загрузок, временные распаковки и скачанные "
            "файлы обновлений?\n\n"
            "Недокачанные файлы будут загружены заново при следующей установке. "
            "Моды, миры и версии останутся на месте.",
            "Очистка кэша",
        ):
            return
        self._run_clean("Очистка кэша", cache=True)

    def _clean_logs(self) -> None:
        if not ask_yes_no(
            self,
            "Удалить журналы лаунчера старше 7 дней?",
            "Очистка журналов",
        ):
            return
        self._run_clean("Очистка журналов", logs=True)

    def _clean_versions(self) -> None:
        report = self._report
        if report is None:
            show_info(self, "Сначала дождитесь подсчёта размеров.")
            return
        if not report.unused_versions:
            show_info(
                self,
                "Неиспользуемых версий нет: все установленные версии нужны "
                "хотя бы одному экземпляру.",
            )
            return
        listing = "\n".join(f"• {item}" for item in report.unused_versions[:15])
        if len(report.unused_versions) > 15:
            listing += f"\n… и ещё {len(report.unused_versions) - 15}"
        if not ask_yes_no(
            self,
            "Удалить версии, на которые не ссылается ни один экземпляр?\n\n"
            f"{listing}\n\nИх можно будет скачать заново.",
            "Удаление версий",
        ):
            return
        self._run_clean("Удаление версий", versions=True)

    def _clean_runtimes(self) -> None:
        report = self._report
        if report is None:
            show_info(self, "Сначала дождитесь подсчёта размеров.")
            return
        if not report.unused_runtimes:
            show_info(self, "Лишних скачанных Java нет.")
            return
        listing = "\n".join(f"• {item}" for item in report.unused_runtimes)
        if not ask_yes_no(
            self,
            "Удалить скачанные лаунчером Java, которые не нужны ни одной "
            "используемой версии?\n\n"
            f"{listing}\n\nПри необходимости лаунчер скачает их снова.",
            "Удаление Java",
        ):
            return
        self._run_clean("Удаление Java", runtimes=True)

    def _clean_backups(self) -> None:
        report = self._report
        size = 0
        if report is not None:
            for entry in report.entries:
                if entry.key == "cache-deleted":
                    size = entry.size
                    break
        if size <= 0:
            show_info(self, "Копий удалённых экземпляров нет.")
            return
        if not ask_yes_no(
            self,
            "Удалить копии модов, сохранённые при удалении экземпляров?\n\n"
            f"Освободится примерно {human_size(size)}. "
            "Восстановить эти копии после удаления нельзя.",
            "Копии удалённых экземпляров",
        ):
            return
        self._run_clean("Удаление копий", backups=True)

    # ------------------------------------------------------------------
    def _run_clean(self, label: str, **flags) -> None:
        def work(reporter):
            return self.manager.clean_storage(reporter=reporter, **flags)

        def done(freed) -> None:
            total = sum((freed or {}).values())
            self._status.configure(text=f"Освобождено: {human_size(total)}")
            if total <= 0:
                show_info(self, "Удалять было нечего.")
            self._report = None
            self._scan_async()

        self.run_task(label, work, on_done=done)
