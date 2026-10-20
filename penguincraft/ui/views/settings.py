"""Страница «Настройки»: параметры Java, игры, сети и интерфейса."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from ...core.settings import JVM_PRESET_ORDER, JVM_PRESETS, auto_memory_mb
from ...core.util import human_size, is_chromebook, open_in_file_manager
from ..widgets import ScrollFrame, ask_yes_no, show_info
from .base import BaseView

THEME_CHOICES = [("dark", "Тёмная"), ("light", "Светлая")]


class SettingsView(BaseView):
    title = "Настройки"
    subtitle = "Java, память, разрешение и поведение лаунчера"

    def build(self) -> None:
        scroll = ScrollFrame(self, self.theme, padding=(22, 16, 22, 18))
        scroll.pack(fill="both", expand=True)
        body = scroll.body
        body.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(body)
        notebook.grid(row=0, column=0, sticky="nsew")

        self._build_general(notebook)
        self._build_java(notebook)
        self._build_game(notebook)
        self._build_mods(notebook)
        self._build_network(notebook)

        footer = ttk.Frame(body, style="TFrame")
        footer.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(footer, text="Сохранить", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(footer, text="Сбросить к авто", command=self._reset_memory).pack(side="right", padx=(8, 0))
        self.saved_hint = ttk.Label(footer, text="", style="Muted.TLabel")
        self.saved_hint.pack(side="left")

        self._load_into_widgets()

    # ------------------------------------------------------------------
    def _section(self, notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(notebook, style="TFrame", padding=16)
        notebook.add(frame, text=title)
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_general(self, notebook) -> None:
        frame = self._section(notebook, "Основное")
        row = 0

        ttk.Label(frame, text="Тема оформления", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.theme_var = tk.StringVar()
        theme_row = ttk.Frame(frame, style="TFrame")
        theme_row.grid(row=row, column=1, sticky="w", pady=8)
        for value, label in THEME_CHOICES:
            ttk.Radiobutton(theme_row, text=label, value=value, variable=self.theme_var).pack(side="left", padx=(0, 12))
        row += 1

        self.snapshots_var = tk.BooleanVar()
        self.old_var = tk.BooleanVar()
        self.confirm_var = tk.BooleanVar()
        for text, var in (
            ("Показывать снапшоты в списке версий", self.snapshots_var),
            ("Показывать устаревшие версии (беты и альфы)", self.old_var),
            ("Спрашивать подтверждение при удалении", self.confirm_var),
        ):
            ttk.Checkbutton(frame, text=text, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
            row += 1

        ttk.Label(frame, text="Параллельных загрузок", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.parallel_var = tk.IntVar()
        ttk.Spinbox(frame, from_=1, to=32, textvariable=self.parallel_var, width=6).grid(row=row, column=1, sticky="w", pady=8)
        row += 1

        ttk.Label(frame, text="Ограничение скорости (КБ/с, 0 — без лимита)", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.limit_var = tk.IntVar()
        ttk.Spinbox(
            frame, from_=0, to=102400, increment=64,
            textvariable=self.limit_var, width=8,
        ).grid(row=row, column=1, sticky="w", pady=8)
        row += 1

        ttk.Label(frame, text="Масштаб интерфейса", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.scale_var = tk.StringVar()
        self.scale_combo = ttk.Combobox(
            frame, state="readonly", width=12,
            values=["100 %", "110 %", "125 %", "150 %", "175 %", "200 %"],
            textvariable=self.scale_var,
        )
        self.scale_combo.grid(row=row, column=1, sticky="w", pady=8)
        row += 1

        ttk.Label(frame, text="Закрывать лаунчер после запуска игры", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.close_var = tk.BooleanVar()
        ttk.Checkbutton(frame, variable=self.close_var).grid(row=row, column=1, sticky="w", pady=8)

    def _build_mods(self, notebook) -> None:
        frame = self._section(notebook, "Моды и обновления")
        row = 0

        self.deps_var = tk.BooleanVar()
        ttk.Checkbutton(
            frame, text="Устанавливать обязательные зависимости модов автоматически",
            variable=self.deps_var,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1

        self.auto_update_var = tk.BooleanVar()
        ttk.Checkbutton(
            frame, text="Автоматически обновлять моды при запуске лаунчера",
            variable=self.auto_update_var,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1

        ttk.Label(frame, text="Проверять обновления не чаще", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        hours_row = ttk.Frame(frame, style="TFrame")
        hours_row.grid(row=row, column=1, sticky="w", pady=8)
        self.hours_var = tk.IntVar()
        ttk.Spinbox(hours_row, from_=1, to=720, textvariable=self.hours_var, width=6).pack(side="left")
        ttk.Label(hours_row, text="ч", style="Muted.TLabel").pack(side="left", padx=(6, 0))
        row += 1

        self.check_launcher_var = tk.BooleanVar()
        ttk.Checkbutton(
            frame, text="Проверять обновления самого лаунчера", variable=self.check_launcher_var
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1

        buttons = ttk.Frame(frame, style="TFrame")
        buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            buttons, text="Проверить обновления лаунчера",
            command=self._check_launcher_update,
        ).pack(side="left")
        ttk.Button(
            buttons, text="Обновить моды сейчас",
            command=self._update_mods_now,
        ).pack(side="left", padx=(8, 0))

    def _build_java(self, notebook) -> None:
        frame = self._section(notebook, "Java и память")
        row = 0

        ttk.Label(frame, text="Профиль JVM", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.preset_var = tk.StringVar()
        preset_titles = [JVM_PRESETS[key]["title"] for key in JVM_PRESET_ORDER]
        self.preset_combo = ttk.Combobox(frame, state="readonly", values=preset_titles, textvariable=self.preset_var)
        self.preset_combo.grid(row=row, column=1, sticky="ew", pady=8)
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _e: self._preset_changed())
        row += 1

        self.preset_hint = ttk.Label(frame, text="", style="Muted.TLabel", wraplength=560, justify="left")
        self.preset_hint.grid(row=row, column=1, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(frame, text="Объём памяти", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.memory_mode_var = tk.StringVar()
        mode_row = ttk.Frame(frame, style="TFrame")
        mode_row.grid(row=row, column=1, sticky="w", pady=8)
        ttk.Radiobutton(mode_row, text="Автоматически", value="auto", variable=self.memory_mode_var, command=self._memory_mode_changed).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(mode_row, text="Вручную", value="manual", variable=self.memory_mode_var, command=self._memory_mode_changed).pack(side="left")
        row += 1

        ttk.Label(frame, text="МиБ (только для ручного режима)", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.memory_var = tk.IntVar()
        self.memory_spin = ttk.Spinbox(frame, from_=512, to=65536, increment=256, textvariable=self.memory_var, width=8)
        self.memory_spin.grid(row=row, column=1, sticky="w", pady=8)
        row += 1

        ttk.Label(frame, text="Путь к Java (пусто = автоматически)", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        java_row = ttk.Frame(frame, style="TFrame")
        java_row.grid(row=row, column=1, sticky="ew", pady=8)
        java_row.columnconfigure(0, weight=1)
        self.java_var = tk.StringVar()
        ttk.Entry(java_row, textvariable=self.java_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(java_row, text="Обзор…", command=self._browse_java).grid(row=0, column=1, padx=(8, 0))
        row += 1

        self.auto_java_var = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Скачивать нужную Java автоматически", variable=self.auto_java_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1

        ttk.Label(frame, text="Дополнительные аргументы JVM", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.jvm_args_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.jvm_args_var).grid(row=row, column=1, sticky="ew", pady=8)
        row += 1

        ttk.Button(frame, text="Показать найденные Java", style="Ghost.TButton", command=self._show_java).grid(row=row, column=1, sticky="w", pady=(6, 0))

    def _build_game(self, notebook) -> None:
        frame = self._section(notebook, "Игра")
        row = 0

        ttk.Label(frame, text="Разрешение окна (ширина × высота)", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        res_row = ttk.Frame(frame, style="TFrame")
        res_row.grid(row=row, column=1, sticky="w", pady=8)
        self.width_var = tk.IntVar()
        self.height_var = tk.IntVar()
        ttk.Spinbox(res_row, from_=320, to=7680, increment=10, textvariable=self.width_var, width=7).pack(side="left")
        ttk.Label(res_row, text="×").pack(side="left", padx=6)
        ttk.Spinbox(res_row, from_=240, to=4320, increment=10, textvariable=self.height_var, width=7).pack(side="left")
        row += 1

        self.fullscreen_var = tk.BooleanVar()
        self.demo_var = tk.BooleanVar()
        self.software_var = tk.BooleanVar()
        for text, var in (
            ("Запускать в полноэкранном режиме", self.fullscreen_var),
            ("Демонстрационный режим (демо-версия)", self.demo_var),
            ("Программный OpenGL (для очень слабых машин)", self.software_var),
        ):
            ttk.Checkbutton(frame, text=text, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
            row += 1

        ttk.Label(frame, text="Дополнительные аргументы игры", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.game_args_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.game_args_var).grid(row=row, column=1, sticky="ew", pady=8)
        row += 1

        ttk.Label(frame, text="Пресеты графики применяются к options.txt экземпляра.", style="Muted.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_network(self, notebook) -> None:
        frame = self._section(notebook, "Сеть и данные")
        row = 0

        ttk.Label(frame, text="Прокси (http://host:port)", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        self.proxy_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.proxy_var).grid(row=row, column=1, sticky="ew", pady=8)
        row += 1

        ttk.Label(frame, text="Каталог данных", style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=8, padx=(0, 14))
        ttk.Label(frame, text=str(self.manager.paths.root), style="TLabel").grid(row=row, column=1, sticky="w", pady=8)
        row += 1

        buttons = ttk.Frame(frame, style="TFrame")
        buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Открыть каталог данных", command=lambda: open_in_file_manager(self.manager.paths.root)).pack(side="left")
        ttk.Button(buttons, text="Очистить кэш загрузок", command=self._clear_downloads).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Экспорт диагностики…", command=self._export_diagnostics).pack(side="left", padx=(8, 0))
        row += 1

        self.cache_label = ttk.Label(frame, text="", style="Muted.TLabel")
        self.cache_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._update_cache_label()

        if is_chromebook():
            ttk.Label(
                frame,
                text="Обнаружен ChromeOS (Crostini): включены экономные настройки.",
                style="Muted.TLabel",
            ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._load_into_widgets()
        self._update_cache_label()

    def _load_into_widgets(self) -> None:
        settings = self.settings
        for value, _label in THEME_CHOICES:
            if value == settings.theme:
                self.theme_var.set(value)
        if not self.theme_var.get():
            self.theme_var.set("dark")
        self.snapshots_var.set(bool(settings.show_snapshots))
        self.old_var.set(bool(settings.show_old_versions))
        self.confirm_var.set(bool(settings.confirm_delete))
        self.parallel_var.set(int(settings.parallel_downloads))
        self.limit_var.set(int(settings.download_limit_kbps))
        self.close_var.set(bool(settings.close_on_launch))

        preset = JVM_PRESETS.get(settings.jvm_preset, JVM_PRESETS["balanced"])
        self.preset_var.set(preset["title"])
        self.preset_hint.configure(text=preset.get("description", ""))
        self.memory_mode_var.set(settings.memory_mode)
        self.memory_var.set(int(settings.memory_mb))
        self.java_var.set(settings.java_path)
        self.auto_java_var.set(bool(settings.auto_download_java))
        self.jvm_args_var.set(settings.custom_jvm_args)

        self.width_var.set(int(settings.resolution_width))
        self.height_var.set(int(settings.resolution_height))
        self.fullscreen_var.set(bool(settings.fullscreen))
        self.demo_var.set(bool(settings.demo_mode))
        self.software_var.set(bool(settings.force_lwjgl_software))
        self.game_args_var.set(settings.game_args)

        self.proxy_var.set(settings.proxy)
        self.scale_var.set(self._scale_label(float(settings.ui_scale or 1.0)))
        self.deps_var.set(bool(settings.install_dependencies))
        self.auto_update_var.set(bool(settings.auto_update_mods))
        self.hours_var.set(int(settings.auto_update_hours or 24))
        self.check_launcher_var.set(bool(settings.check_launcher_updates))
        self._memory_mode_changed()

    # ------------------------------------------------------------------
    @staticmethod
    def _scale_label(value: float) -> str:
        percent = int(round(float(value) * 100))
        return f"{percent} %"

    @staticmethod
    def _scale_value(label: str) -> float:
        digits = "".join(ch for ch in str(label) if ch.isdigit())
        try:
            percent = int(digits) if digits else 100
        except ValueError:
            percent = 100
        return max(0.75, min(percent / 100.0, 2.0))

    def _check_launcher_update(self) -> None:
        def work(reporter):
            reporter.status("Проверка обновлений лаунчера…")
            return self.manager.check_launcher_update(raise_errors=True)

        def done(release) -> None:
            from ... import APP_VERSION

            if release is None:
                show_info(self.app, "Не удалось проверить обновления (нет сети).")
                return
            if release.is_newer:
                self.app._notify_update(release)
            else:
                show_info(self.app, f"У вас последняя версия ({APP_VERSION}).")

        def failed(error) -> None:
            show_info(
                self.app,
                "Не удалось проверить обновления.\n\n"
                f"{error}\n\n"
                "GitHub ограничивает частоту анонимных запросов. "
                "Попробуйте позже или откройте страницу релизов вручную.",
                "Обновление лаунчера",
            )

        self.run_task("Проверка обновлений", work, on_done=done, on_error=failed)

    def _update_mods_now(self) -> None:
        def work(reporter):
            return self.manager.auto_update_mods(reporter=reporter, force=True)

        def done(counters) -> None:
            if not counters.get("instances"):
                show_info(self.app, "Экземпляров с модами не найдено.")
                return
            show_info(
                self.app,
                f"Проверено экземпляров: {counters['instances']}\n"
                f"Обновлено проектов: {counters['updated']}\n"
                f"Ошибок: {counters['failed']}",
                "Обновление модов",
            )
            self.app.refresh_all()

        self.run_task("Обновление модов", work, on_done=done)

    def _memory_mode_changed(self) -> None:
        manual = self.memory_mode_var.get() == "manual"
        self.memory_spin.configure(state=("normal" if manual else "disabled"))

    def _preset_changed(self) -> None:
        title = self.preset_var.get()
        for key in JVM_PRESET_ORDER:
            if JVM_PRESETS[key]["title"] == title:
                self.preset_hint.configure(text=JVM_PRESETS[key].get("description", ""))
                if self.memory_mode_var.get() == "auto":
                    self.memory_var.set(auto_memory_mb(key))
                break

    def _browse_java(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.app,
            title="Выберите исполняемый файл java",
            filetypes=[("Java", "java"), ("Все файлы", "*.*")],
            initialdir=os.path.expanduser("~"),
        )
        if path:
            self.java_var.set(path)

    def _show_java(self) -> None:
        runtimes = self.manager.java_runtimes()
        if not runtimes:
            show_info(self.app, "Java не найдена. Лаунчер скачает её автоматически при первом запуске.")
            return
        lines = [f"{item.title}: {item.path}" for item in runtimes]
        show_info(self.app, "\n".join(lines), "Найденные Java")

    def _reset_memory(self) -> None:
        self.memory_mode_var.set("auto")
        self.memory_var.set(auto_memory_mb(self._current_preset_key()))
        self._memory_mode_changed()

    def _current_preset_key(self) -> str:
        title = self.preset_var.get()
        for key in JVM_PRESET_ORDER:
            if JVM_PRESETS[key]["title"] == title:
                return key
        return "balanced"

    # ------------------------------------------------------------------
    def _save(self) -> None:
        settings = self.settings
        settings.show_snapshots = bool(self.snapshots_var.get())
        settings.show_old_versions = bool(self.old_var.get())
        settings.confirm_delete = bool(self.confirm_var.get())
        settings.close_on_launch = bool(self.close_var.get())
        try:
            settings.parallel_downloads = max(1, min(int(self.parallel_var.get()), 32))
        except (tk.TclError, ValueError):
            pass
        try:
            new_limit = max(0, min(int(self.limit_var.get()), 102400))
        except (tk.TclError, ValueError):
            new_limit = settings.download_limit_kbps
        limit_changed = new_limit != int(settings.download_limit_kbps)
        settings.download_limit_kbps = new_limit

        settings.jvm_preset = self._current_preset_key()
        settings.memory_mode = self.memory_mode_var.get() or "auto"
        try:
            settings.memory_mb = max(512, int(self.memory_var.get()))
        except (tk.TclError, ValueError):
            pass
        settings.java_path = self.java_var.get().strip()
        settings.auto_download_java = bool(self.auto_java_var.get())
        settings.custom_jvm_args = self.jvm_args_var.get().strip()

        try:
            settings.resolution_width = max(320, int(self.width_var.get()))
            settings.resolution_height = max(240, int(self.height_var.get()))
        except (tk.TclError, ValueError):
            pass
        settings.fullscreen = bool(self.fullscreen_var.get())
        settings.demo_mode = bool(self.demo_var.get())
        settings.force_lwjgl_software = bool(self.software_var.get())
        settings.game_args = self.game_args_var.get().strip()

        new_proxy = self.proxy_var.get().strip()
        proxy_changed = new_proxy != settings.proxy
        settings.proxy = new_proxy

        settings.install_dependencies = bool(self.deps_var.get())
        settings.auto_update_mods = bool(self.auto_update_var.get())
        try:
            settings.auto_update_hours = max(1, min(int(self.hours_var.get()), 720))
        except (tk.TclError, ValueError):
            pass
        settings.check_launcher_updates = bool(self.check_launcher_var.get())

        new_scale = self._scale_value(self.scale_var.get())
        scale_changed = abs(new_scale - float(settings.ui_scale or 1.0)) > 0.001
        settings.ui_scale = new_scale

        self.manager.save_settings()
        if proxy_changed or limit_changed:
            self.manager.reload_http()
        self.saved_hint.configure(text="Сохранено")
        self.app.update_header_status("")
        self.app.refresh_all()
        if scale_changed:
            self.app.apply_ui_scale()
            return
        if self.theme_var.get() and self.theme_var.get() != self.app.theme.name:
            self.app.set_theme(self.theme_var.get())

    def _update_cache_label(self) -> None:
        try:
            total = sum(
                item.stat().st_size
                for item in self.manager.paths.downloads.glob("*")
                if item.is_file()
            )
            self.cache_label.configure(text=f"Временные загрузки: {human_size(total)}")
        except OSError:
            self.cache_label.configure(text="")

    def _clear_downloads(self) -> None:
        if not ask_yes_no(self.app, "Удалить временные файлы загрузок?", "Очистка"):
            return
        from ...core.util import remove_tree

        remove_tree(self.manager.paths.downloads)
        self.manager.paths.downloads.mkdir(parents=True, exist_ok=True)
        self._update_cache_label()
        self.log("Кэш загрузок очищен")

    def _export_diagnostics(self) -> None:
        from ...core import diagnostics as diagnostics_module

        target = filedialog.asksaveasfilename(
            parent=self.app,
            title="Куда сохранить диагностику",
            defaultextension=".zip",
            initialfile=diagnostics_module.default_name(),
            filetypes=[("Архив ZIP", "*.zip"), ("Все файлы", "*.*")],
        )
        if not target:
            return

        def work(reporter):
            return self.manager.export_diagnostics(target, reporter=reporter)

        def done(path) -> None:
            show_info(
                self.app,
                "Диагностика сохранена:\n"
                f"{path}\n\n"
                "В архив не попадают аккаунты и пароли — только версия, "
                "система, настройки, журналы и отчёты о падениях.",
                "Экспорт диагностики",
            )

        self.run_task("Экспорт диагностики", work, on_done=done)
