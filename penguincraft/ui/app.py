"""Главное окно PenguinCraft: боковая навигация, статус и фоновые задачи.

tkinter не потокобезопасен, поэтому все длительные операции выполняются в
отдельном потоке и общаются с интерфейсом только через очередь событий.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import ttk
from typing import Callable, List, Optional, Tuple

from .. import APP_VERSION
from ..core.task import Cancelled, Event, Reporter
from ..manager import LauncherManager
from .theme import Theme
from .widgets import center, show_error, show_info

# ---------------------------------------------------------------------------
# Навигация
# ---------------------------------------------------------------------------

#: (ключ, подпись в меню, класс представления)
NAV_ITEMS: List[Tuple[str, str]] = [
    ("home", "Главная"),
    ("instances", "Экземпляры"),
    ("versions", "Версии"),
    ("mods", "Моды"),
    ("modpacks", "Модпаки"),
    ("accounts", "Аккаунты"),
    ("settings", "Настройки"),
    ("console", "Консоль"),
]


class _Task:
    __slots__ = ("label", "reporter", "on_done", "on_error")

    def __init__(self, label, reporter, on_done, on_error) -> None:
        self.label = label
        self.reporter = reporter
        self.on_done = on_done
        self.on_error = on_error


class LauncherApp(tk.Tk):
    """Корневое окно приложения."""

    def __init__(self, manager: LauncherManager) -> None:
        # className важен для WM_CLASS: по нему ChromeOS/рабочий стол
        # сопоставляет окно с ярлыком (StartupWMClass).
        super().__init__(className="Penguincraft")
        self.manager = manager
        self.settings = manager.settings
        self.log_lines: deque = deque(maxlen=6000)
        self._events: "queue.Queue" = queue.Queue()
        self._views: dict = {}
        self._nav_buttons: dict = {}
        self._current_key = ""
        self._current_task: Optional[_Task] = None
        self._progress_indeterminate = False
        self._icon_cache = None
        self._closing = False
        self._dpi_scaling: Optional[float] = None

        self.title("PenguinCraft — лаунчер Minecraft")
        self.minsize(920, 580)
        center(self, int(self.settings.window_width), int(self.settings.window_height))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        self._apply_scaling()
        self._build_theme()
        self._build_ui()
        self.select("home")
        self.after(60, self._poll)
        self.after(1500, self._startup_checks)

    # ------------------------------------------------------------------
    # Масштаб интерфейса
    # ------------------------------------------------------------------
    def _base_scaling(self) -> float:
        """Собственный масштаб Tk (зависит от DPI экрана)."""
        if self._dpi_scaling is None:
            try:
                self._dpi_scaling = float(self.tk.call("tk", "scaling"))
            except tk.TclError:  # pragma: no cover
                self._dpi_scaling = 1.0
        return self._dpi_scaling

    def _apply_scaling(self) -> None:
        factor = self._base_scaling() * max(0.5, min(float(self.settings.ui_scale or 1.0), 2.0))
        try:
            self.tk.call("tk", "scaling", factor)
        except tk.TclError:  # pragma: no cover
            pass

    def apply_ui_scale(self) -> None:
        """Применяет новый масштаб (после смены настройки) и пересобирает окно."""
        if self._current_task is not None:
            show_info(self, "Дождитесь завершения текущей операции, затем смените масштаб.")
            return
        self._apply_scaling()
        key = self._current_key or "home"
        for child in self.winfo_children():
            child.destroy()
        self._views.clear()
        self._nav_buttons.clear()
        self._build_theme()
        self._build_ui()
        self.select(key)

    # ------------------------------------------------------------------
    # Стартовые проверки (обновления лаунчера и модов)
    # ------------------------------------------------------------------
    def _startup_checks(self) -> None:
        if self._closing:
            return
        if self.settings.check_launcher_updates:
            self._check_launcher_update_quiet()
        if self.settings.auto_update_mods:
            self._maybe_auto_update_mods()

    def _check_launcher_update_quiet(self, *, force: bool = False) -> None:
        import time as _time

        settings = self.settings
        now = _time.time()
        if not force and settings.last_update_check and now - settings.last_update_check < 86400:
            return
        if self._current_task is not None:
            return

        def work(reporter):
            reporter.status("Проверка обновлений лаунчера…")
            return self.manager.check_launcher_update()

        def done(release) -> None:
            settings.last_update_check = now
            try:
                self.manager.save_settings()
            except OSError:
                pass
            if release is None or not release.is_newer:
                return
            self.log(f"Доступна новая версия PenguinCraft: {release.version}")
            self.set_status(f"Доступна версия {release.version}", "warning")
            self._notify_update(release)

        self.run_task("Проверка обновлений", work, on_done=done)

    def _notify_update(self, release) -> None:
        from .widgets import ask_yes_no
        import webbrowser

        if ask_yes_no(
            self,
            f"Доступна новая версия PenguinCraft: {release.version}\n"
            f"У вас установлена {APP_VERSION}.\n\n"
            "Открыть страницу загрузки?",
            "Обновление лаунчера",
        ):
            try:
                webbrowser.open(release.url)
            except Exception:  # noqa: BLE001
                pass

    def _maybe_auto_update_mods(self) -> None:
        if self._current_task is not None or self.manager.game_running:
            return

        def work(reporter):
            return self.manager.auto_update_mods(reporter=reporter)

        def done(counters) -> None:
            if counters.get("updated"):
                self.log(
                    f"Автообновление модов: обновлено {counters['updated']} "
                    f"в {counters['checked']} экземплярах"
                )
                self.set_status(
                    f"Моды обновлены: {counters['updated']}", "success"
                )
                self.refresh_all()

        self.run_task("Автообновление модов", work, on_done=done)

    # ------------------------------------------------------------------
    # Сборка интерфейса
    # ------------------------------------------------------------------
    def _build_theme(self) -> None:
        self.theme = Theme(self, self.settings.theme)

    def _set_window_icon(self) -> None:
        """Ставит иконку окна.  Отсутствие файла не считается ошибкой."""
        path = Path(__file__).with_name("assets") / "penguincraft.png"
        self._icon_photo = None
        try:
            self._icon_photo = tk.PhotoImage(file=str(path))
            self.iconphoto(True, self._icon_photo)
        except Exception:  # noqa: BLE001 - иконка не критична
            self._icon_photo = None

    def _build_ui(self) -> None:
        container = ttk.Frame(self, style="TFrame")
        container.pack(fill="both", expand=True)

        self.sidebar = ttk.Frame(container, style="Sidebar.TFrame", width=int(self.settings.sidebar_width))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        right = ttk.Frame(container, style="TFrame")
        right.pack(side="left", fill="both", expand=True)
        self._build_header(right)

        self._build_footer(right)

        self.content = ttk.Frame(right, style="TFrame")
        self.content.pack(fill="both", expand=True)
        self._build_views()

    def _build_sidebar(self) -> None:
        head = ttk.Frame(self.sidebar, style="Sidebar.TFrame", padding=(16, 18, 16, 8))
        head.pack(fill="x")
        ttk.Label(head, text="PenguinCraft", style="SidebarTitle.TLabel").pack(anchor="w")
        ttk.Label(head, text=f"версия {APP_VERSION}", style="SidebarMuted.TLabel").pack(anchor="w")

        nav = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        nav.pack(fill="x", pady=(8, 0))
        for key, title in NAV_ITEMS:
            button = ttk.Button(nav, text=title, style="Nav.TButton", command=lambda k=key: self.select(k))
            button.pack(fill="x")
            self._nav_buttons[key] = button

        foot = ttk.Frame(self.sidebar, style="Sidebar.TFrame", padding=(16, 12))
        foot.pack(side="bottom", fill="x")
        ttk.Separator(foot, orient="horizontal").pack(fill="x", pady=(0, 8))
        ttk.Label(foot, text="Активный аккаунт", style="SidebarMuted.TLabel").pack(anchor="w")
        self.account_label = ttk.Label(
            foot,
            text="—",
            style="SidebarMuted.TLabel",
            wraplength=max(120, int(self.settings.sidebar_width) - 32),
            justify="left",
        )
        self.account_label.pack(anchor="w", pady=(2, 0))
        self._update_account_chip()

    def _build_header(self, parent) -> None:
        header = ttk.Frame(parent, style="TFrame", padding=(22, 16, 22, 10))
        header.pack(fill="x")
        left = ttk.Frame(header, style="TFrame")
        left.pack(side="left", fill="x", expand=True)
        self.page_title = ttk.Label(left, text="", style="PageTitle.TLabel")
        self.page_title.pack(anchor="w")
        self.page_subtitle = ttk.Label(left, text="", style="PageSubtitle.TLabel")
        self.page_subtitle.pack(anchor="w", pady=(2, 0))
        right = ttk.Frame(header, style="TFrame")
        right.pack(side="right")
        self.header_status = ttk.Label(right, text="", style="Muted.TLabel", justify="right")
        self.header_status.pack(anchor="e")
        ttk.Separator(parent, orient="horizontal").pack(fill="x")

    def _build_footer(self, parent) -> None:
        footer = ttk.Frame(parent, style="Footer.TFrame", padding=(16, 8))
        footer.pack(side="bottom", fill="x")
        self.cancel_button = ttk.Button(
            footer, text="Отменить", style="Ghost.TButton", command=self.cancel_task
        )
        self.cancel_button.pack(side="right", padx=(10, 0))
        self.cancel_button.state(["disabled"])
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=1000, length=190)
        self.progress.pack(side="right", padx=(10, 0))
        self.status_label = ttk.Label(footer, text="Готово к работе", style="FooterMuted.TLabel", anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)

    def _build_views(self) -> None:
        from .views import build_view

        for key, _title in NAV_ITEMS:
            view = build_view(key, self.content, self)
            view.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._views[key] = view

    # ------------------------------------------------------------------
    # Навигация
    # ------------------------------------------------------------------
    def select(self, key: str) -> None:
        view = self._views.get(key)
        if view is None:
            return
        self._current_key = key
        view.tkraise()
        for nav_key, button in self._nav_buttons.items():
            button.configure(style="NavActive.TButton" if nav_key == key else "Nav.TButton")
        self.page_title.configure(text=view.title)
        self.page_subtitle.configure(text=getattr(view, "subtitle", ""))
        try:
            view.on_show()
        except Exception as error:  # noqa: BLE001 - представление не должно ронять окно
            show_error(self, error)

    @property
    def current_view(self):
        return self._views.get(self._current_key)

    def refresh_active(self) -> None:
        view = self.current_view
        if view is not None:
            try:
                view.on_refresh()
            except Exception:  # noqa: BLE001
                pass

    def refresh_all(self) -> None:
        for view in self._views.values():
            try:
                view.on_refresh()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Тема
    # ------------------------------------------------------------------
    def set_theme(self, name: str) -> None:
        if self._current_task is not None:
            show_info(self, "Дождитесь завершения текущей операции, затем смените тему.")
            return
        self.settings.theme = name
        self.manager.save_settings()
        key = self._current_key or "home"
        for child in self.winfo_children():
            child.destroy()
        self._views.clear()
        self._nav_buttons.clear()
        self._build_theme()
        self._build_ui()
        self.select(key)

    # ------------------------------------------------------------------
    # Фоновые задачи
    # ------------------------------------------------------------------
    def run_task(
        self,
        label: str,
        work: Callable[[Reporter], object],
        *,
        on_done: Optional[Callable[[object], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
        cancellable: bool = True,
        status: str = "",
    ) -> bool:
        """Запускает *work* в фоне.  Возвращает ``False``, если занято."""
        if self._current_task is not None:
            show_info(self, "Уже выполняется другая операция.")
            return False
        reporter = Reporter(self._emit_event)
        self._current_task = _Task(label, reporter, on_done, on_error)
        self._set_busy(True, status or label)
        self.log(f"▶ {label}")

        def runner() -> None:
            try:
                result = work(reporter)
            except Cancelled:
                self.post(self._finish, label, None, "cancelled")
            except BaseException as error:  # noqa: BLE001 - показываем пользователю
                self.post(self._finish, label, error, "error")
            else:
                self.post(self._finish, label, result, "done")

        threading.Thread(target=runner, name="penguincraft-task", daemon=True).start()
        return True

    def cancel_task(self) -> None:
        task = self._current_task
        if task is None:
            return
        task.reporter.cancel()
        self.set_status("Отмена операции…", "warning")

    # ------------------------------------------------------------------
    # Запуск игры (общий для «Главной» и «Консоли»)
    # ------------------------------------------------------------------
    def start_game(self, instance, account=None) -> None:
        if self.manager.game_running:
            show_info(self, "Игра уже запущена.")
            return
        self.settings.last_instance = instance.name
        if account is not None:
            self.settings.last_account = account.id
        self.manager.save_settings()

        def work(reporter):
            try:
                issues = self.manager.check_instance_health(instance)
            except Exception:  # noqa: BLE001
                issues = {}
            for key, items in (issues or {}).items():
                for item in items:
                    reporter.log(f"Внимание: {item}")
            return self.manager.launch(
                instance,
                account,
                reporter=reporter,
                on_line=lambda line: self.post(self.log, line),
                on_exit=lambda code: self.post(self._game_exited, code),
            )

        def done(process) -> None:
            self.log(f"Игра запущена (PID {process.pid})")
            self.on_game_state_changed()
            if self.settings.close_on_launch:
                self.log("Лаунчер закроется сразу после запуска игры")
                self.after(700, self._close_after_launch)

        self.run_task(f"Запуск «{instance.display_name}»", work, on_done=done)

    def _close_after_launch(self) -> None:
        if self._current_task is not None:
            return
        try:
            self.settings.window_width = self.winfo_width()
            self.settings.window_height = self.winfo_height()
            self.manager.save_settings()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def stop_game(self) -> None:
        self.manager.stop_game()
        self.set_status("Остановка игры…", "warning")

    def _game_exited(self, code: int) -> None:
        self.log(f"Игра завершилась с кодом {code}")
        if code != 0:
            self._report_crash(code)
        self.on_game_state_changed()
        self.refresh_all()

    def _report_crash(self, code: int) -> None:
        """Показывает отчёт о падении игры, если он появился."""
        from .widgets import ask_yes_no, show_warning
        from ..core.util import open_in_file_manager

        report = None
        process = self.manager.process
        if process is not None:
            try:
                report = process.crash_report()
            except Exception:  # noqa: BLE001
                report = None
        if report is not None:
            self.log(f"Отчёт о падении: {report}")
            show_warning(
                self,
                f"Игра завершилась с ошибкой (код {code}).\n\n"
                f"Найден отчёт о падении:\n{report.name}",
                "Игра упала",
            )
            if ask_yes_no(self, "Показать отчёт о падении?", "Игра упала"):
                self.show_crash_report(report)
            elif ask_yes_no(self, "Открыть папку с отчётами о падениях?", "Игра упала"):
                open_in_file_manager(report.parent)
        else:
            self.set_status(f"Игра завершилась с кодом {code}", "error")
            self.log("Отчёт о падении не найден — подробности в разделе «Консоль»")

    def show_crash_report(self, path=None) -> None:
        """Открывает окно с текстом отчёта о падении."""
        from pathlib import Path

        from .widgets import CrashReportWindow, show_info

        if path is None:
            process = self.manager.process
            try:
                path = process.crash_report() if process is not None else None
            except Exception:  # noqa: BLE001
                path = None
        if path is None:
            path = self._newest_crash_report()
        if path is None:
            show_info(self, "Отчёт о падении не найден.", "Отчёты о падениях")
            return
        CrashReportWindow(self, Path(path), self.theme)

    def _newest_crash_report(self):
        """Самый свежий отчёт о падении среди экземпляров."""
        newest = None
        newest_time = 0.0
        try:
            instances = self.manager.instances.list()
        except Exception:  # noqa: BLE001
            instances = []
        for instance in instances:
            directory = self.manager.instances.game_dir(instance.name) / "crash-reports"
            if not directory.is_dir():
                continue
            for entry in directory.glob("*.txt"):
                try:
                    modified = entry.stat().st_mtime
                except OSError:
                    continue
                if modified > newest_time:
                    newest_time = modified
                    newest = entry
        return newest

    def _finish(self, label: str, payload, kind: str) -> None:
        task = self._current_task
        self._current_task = None
        self._set_busy(False)
        if kind == "error":
            self.set_status(f"{label}: ошибка", "error")
            self.log(f"✗ {label}: {payload}")
            show_error(self, payload)
            if task and task.on_error is not None:
                try:
                    task.on_error(payload)
                except Exception:  # noqa: BLE001
                    pass
            return
        if kind == "cancelled":
            self.set_status(f"{label}: отменено", "warning")
            self.log(f"■ {label}: отменено")
            return
        self.set_status(f"{label}: готово", "success")
        self.log(f"✓ {label}")
        if task and task.on_done is not None:
            try:
                task.on_done(payload)
            except Exception as error:  # noqa: BLE001
                show_error(self, error)
        self.refresh_active()

    def _set_busy(self, busy: bool, status: str = "") -> None:
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(14)
            self._progress_indeterminate = True
            self.cancel_button.state(["!disabled"])
            if status:
                self.set_status(status, "busy")
        else:
            if self._progress_indeterminate:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._progress_indeterminate = False
            self.progress["value"] = 0
            self.cancel_button.state(["disabled"])

    # ------------------------------------------------------------------
    # Очередь событий
    # ------------------------------------------------------------------
    def post(self, func: Callable, *args) -> None:
        self._events.put(("__call__", func, args))

    def _emit_event(self, event: Event) -> None:
        self._events.put(event)

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            while True:
                item = self._events.get_nowait()
                if isinstance(item, Event):
                    self._dispatch(item)
                elif isinstance(item, tuple) and item and item[0] == "__call__":
                    _, func, args = item
                    try:
                        func(*args)
                    except Exception:  # noqa: BLE001
                        pass
        except queue.Empty:
            pass
        try:
            self.after(50, self._poll)
        except tk.TclError:  # pragma: no cover - окно уже закрыто
            pass

    def destroy(self) -> None:
        self._closing = True
        try:
            super().destroy()
        except tk.TclError:  # pragma: no cover
            pass

    def _dispatch(self, event: Event) -> None:
        if event.kind == "status":
            self.set_status(event.text, "busy")
        elif event.kind == "progress":
            self._on_progress(event)
        elif event.kind == "log":
            self.log(event.text)
        elif event.kind == "error":
            self.log(f"! {event.text}")
        elif event.kind == "done":
            if event.text:
                self.log(f"✓ {event.text}")
        view = self.current_view
        handler = getattr(view, "on_event", None)
        if handler is not None:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                pass

    def _on_progress(self, event: Event) -> None:
        total = event.total or 0
        if total <= 0:
            return
        if self._progress_indeterminate:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self._progress_indeterminate = False
        fraction = max(0.0, min(1.0, event.current / total))
        self.progress["value"] = fraction * 1000
        if event.detail:
            percent = f"{fraction * 100:.0f}%"
            current = self.status_label.cget("text")
            if not current.startswith(event.detail):
                self.set_status(f"{event.detail} — {percent}", "busy")
            else:
                self.status_label.configure(text=f"{event.detail} — {percent}")

    # ------------------------------------------------------------------
    # Мелочи
    # ------------------------------------------------------------------
    def set_status(self, text: str, kind: str = "info") -> None:
        styles = {
            "busy": "FooterMuted.TLabel",
            "info": "FooterMuted.TLabel",
            "success": "Footer.TLabel",
            "warning": "Footer.TLabel",
            "error": "Footer.TLabel",
        }
        self.status_label.configure(text=text, style=styles.get(kind, "FooterMuted.TLabel"))

    def log(self, text: str) -> None:
        if not text:
            return
        self.log_lines.append(text)
        view = self.current_view
        handler = getattr(view, "on_log", None)
        if handler is not None:
            try:
                handler(text)
            except Exception:  # noqa: BLE001
                pass
        # Консоль обновляем всегда, даже когда она не открыта.
        console = self._views.get("console")
        if console is not None and console is not view and hasattr(console, "on_log"):
            try:
                console.on_log(text)
            except Exception:  # noqa: BLE001
                pass

    def _update_account_chip(self) -> None:
        account = self.manager.accounts.active
        if account is None:
            self.account_label.configure(text="не выбран")
        elif account.expired:
            self.account_label.configure(text=f"{account.display} (сессия истекла)")
        else:
            self.account_label.configure(text=f"{account.display} · {account.type_title}")

    def update_header_status(self, text: str) -> None:
        self.header_status.configure(text=text)

    def load_icon(self, url: str, callback: Callable) -> None:
        if not url:
            return
        if self._icon_cache is None:
            from .images import IconCache

            self._icon_cache = IconCache(self.manager.http, 24)
        self._icon_cache.get(url, callback, lambda fn: self.post(fn))

    # ------------------------------------------------------------------
    def on_game_state_changed(self) -> None:
        """Обновляет индикаторы во всех представлениях."""
        self._update_account_chip()
        if self.manager.game_running:
            process = self.manager.process
            self.header_status.configure(
                text=f"Игра запущена · PID {process.pid}" if process else "Игра запущена"
            )
        else:
            self.header_status.configure(text="")

    def _on_close(self) -> None:
        if self.manager.game_running:
            from .widgets import ask_yes_no

            if not ask_yes_no(
                self,
                "Игра сейчас запущена. Закрыть лаунчер? Игра продолжит работать.",
                "Закрытие",
            ):
                return
        if self._current_task is not None:
            from .widgets import ask_yes_no

            if not ask_yes_no(self, "Операция ещё выполняется. Прервать и выйти?", "Закрытие"):
                return
            self._current_task.reporter.cancel()
        self._closing = True
        try:
            self.settings.window_width = self.winfo_width()
            self.settings.window_height = self.winfo_height()
            self.manager.save_settings()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
