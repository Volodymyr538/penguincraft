"""Страница «Аккаунты»: вход и управление профилями игроков."""

from __future__ import annotations

import webbrowser
import tkinter as tk
from tkinter import ttk

from ..widgets import Card, Table, ask_string, ask_yes_no, show_error, show_info
from .base import BaseView


class MsLoginDialog(tk.Toplevel):
    """Окно с кодом устройства для входа в Microsoft."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Вход в Microsoft")
        self.transient(app)
        self.resizable(False, False)
        self.configure(bg=app.theme.colors["panel"])
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover
            pass

        body = ttk.Frame(self, style="Card.TFrame", padding=20)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Вход в аккаунт Microsoft", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="Откройте страницу входа и введите этот код.\n"
                 "Лаунчер сам завершит вход, как только вы подтвердите вход.",
            style="CardMuted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(6, 12))

        self.code_label = ttk.Label(body, text="— — — —", style="Stat.TLabel")
        self.code_label.pack(anchor="w", pady=(0, 4))
        self.uri_label = ttk.Label(body, text="", style="Card.TLabel")
        self.uri_label.pack(anchor="w", pady=(0, 12))
        self.status_label = ttk.Label(body, text="Ожидание подтверждения…", style="CardMuted.TLabel", wraplength=360, justify="left")
        self.status_label.pack(anchor="w")

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text="Открыть страницу", style="Accent.TButton", command=self._open).pack(side="left")
        ttk.Button(row, text="Отмена", command=self.app.cancel_task).pack(side="right")

        self._uri = ""

    def set_prompt(self, prompt) -> None:
        self.code_label.configure(text=prompt.user_code)
        self.uri_label.configure(text=prompt.verification_uri)
        self._uri = prompt.verification_uri
        self.status_label.configure(text=prompt.message or "Ожидание подтверждения входа…")

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _open(self) -> None:
        if self._uri:
            try:
                webbrowser.open(self._uri)
            except Exception:  # noqa: BLE001
                pass


class ElyByDialog(tk.Toplevel):
    """Окно входа через Ely.by (ник и пароль)."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.result = None
        self.title("Вход через Ely.by")
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

        ttk.Label(body, text="Ely.by", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(body, text="Имя пользователя", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        self.username = ttk.Entry(body, width=28)
        self.username.grid(row=1, column=1, sticky="ew", pady=6, padx=(10, 0))
        ttk.Label(body, text="Пароль", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        self.password = ttk.Entry(body, width=28, show="•")
        self.password.grid(row=2, column=1, sticky="ew", pady=6, padx=(10, 0))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Войти", style="Accent.TButton", command=self._ok).pack(side="right")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.username.focus_set()

    def _ok(self) -> None:
        name = self.username.get().strip()
        password = self.password.get()
        if not name or not password:
            show_error(self, "Заполните имя пользователя и пароль.")
            return
        self.result = (name, password)
        self.destroy()


class AccountsView(BaseView):
    title = "Аккаунты"
    subtitle = "Microsoft, Ely.by и офлайн-профили"

    def build(self) -> None:
        page = self.page()

        toolbar = ttk.Frame(page, style="TFrame")
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="Войти Microsoft", style="Accent.TButton", command=self._ms_login).pack(side="left")
        ttk.Button(toolbar, text="Войти Ely.by", command=self._elyby_login).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Добавить офлайн", command=self._add_offline).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Обновить", style="Ghost.TButton", command=self.reload).pack(side="right")

        card = Card(page, self.theme)
        card.pack(fill="both", expand=True)
        self.table = Table(
            card.body,
            self.theme,
            [
                ("active", "", 34, "center"),
                ("type", "Тип", 120),
                ("name", "Имя", 220),
                ("uuid", "UUID", 300),
                ("state", "Состояние", 140),
            ],
            height=14,
            on_select=lambda _i: self._update_buttons(),
            on_activate=lambda _i: self._set_active(),
        )
        self.table.pack(fill="both", expand=True)

        actions = ttk.Frame(card.body, style="Card.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Сделать активным", command=self._set_active).pack(side="left")
        ttk.Button(actions, text="Обновить сессию", command=self._refresh_session).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Удалить", style="Danger.TButton", command=self._remove).pack(side="left", padx=(8, 0))

        self.hint = ttk.Label(page, text="", style="Muted.TLabel")
        self.hint.pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        active = self.manager.accounts.active
        selected = self.table.selected()
        self.table.clear()
        for account in self.manager.accounts:
            marker = "●" if active is not None and account.id == active.id else ""
            state = "сессия истекла" if account.expired else "активен"
            self.table.add(
                account.id,
                (marker, account.type_title, account.display, account.uuid, state),
            )
        if selected and self.table.tree.exists(selected):
            self.table.tree.selection_set(selected)
        if len(self.manager.accounts) == 0:
            self.hint.configure(text="Аккаунтов пока нет. Офлайн-профиль можно добавить за секунду.")
        else:
            self.hint.configure(text="Кликните по аккаунту, чтобы сделать его активным.")
        self.app.on_game_state_changed()

    def _update_buttons(self) -> None:
        pass

    def _selected(self):
        account_id = self.table.selected()
        return self.manager.accounts.get(account_id) if account_id else None

    # ------------------------------------------------------------------
    def _add_offline(self) -> None:
        name = ask_string(self.app, "Имя игрока (офлайн-профиль):", initial="Steve")
        if not name:
            return
        try:
            account = self.manager.add_offline_account(name)
        except Exception as error:  # noqa: BLE001
            show_error(self.app, error)
            return
        self.log(f"Добавлен офлайн-аккаунт: {account.name}")
        self.reload()
        self.app.refresh_all()

    def _ms_login(self) -> None:
        holder = {"dialog": None}

        def on_prompt(prompt) -> None:
            def show() -> None:
                dialog = MsLoginDialog(self.app)
                dialog.set_prompt(prompt)
                holder["dialog"] = dialog
            self.app.post(show)

        def on_status(text: str) -> None:
            def update() -> None:
                if holder["dialog"] is not None and holder["dialog"].winfo_exists():
                    holder["dialog"].set_status(text)
            self.app.post(update)

        def work(reporter):
            return self.manager.start_microsoft_login(
                reporter=reporter, on_prompt=on_prompt, on_status=on_status
            )

        def done(account) -> None:
            dialog = holder["dialog"]
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
            self.log(f"Вход выполнен: {account.name}")
            self.reload()
            self.app.refresh_all()

        def failed(_error) -> None:
            dialog = holder["dialog"]
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()

        self.run_task("Вход в Microsoft", work, on_done=done, on_error=failed)

    def _elyby_login(self) -> None:
        dialog = ElyByDialog(self.app)
        self.wait_window(dialog)
        if not dialog.result:
            return
        username, password = dialog.result

        def work(reporter):
            reporter.status("Вход через Ely.by…")
            return self.manager.add_elyby_account(username, password, reporter=reporter)

        self.run_task("Вход через Ely.by", work, on_done=lambda _a: self.app.refresh_all())

    def _set_active(self) -> None:
        account = self._selected()
        if account is None:
            show_info(self.app, "Выберите аккаунт.")
            return
        self.manager.accounts.set_active(account)
        self.settings.last_account = account.id
        self.manager.save_settings()
        self.reload()
        self.app.refresh_all()

    def _refresh_session(self) -> None:
        account = self._selected()
        if account is None:
            show_info(self.app, "Выберите аккаунт.")
            return
        self.run_task(
            f"Обновление сессии {account.display}",
            lambda reporter: self.manager.refresh_account(account, reporter),
            on_done=lambda _a: self.app.refresh_all(),
        )

    def _remove(self) -> None:
        account = self._selected()
        if account is None:
            show_info(self.app, "Выберите аккаунт.")
            return
        if not ask_yes_no(self.app, f"Удалить аккаунт «{account.display}»?", "Удаление"):
            return
        self.manager.accounts.remove(account)
        self.reload()
        self.app.refresh_all()
