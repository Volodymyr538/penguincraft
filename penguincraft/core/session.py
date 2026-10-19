"""Поддержание сессии аккаунта в рабочем состоянии."""

from __future__ import annotations

from typing import Optional

from ..auth.account import Account, AccountType
from ..auth.microsoft import MicrosoftAuth, MicrosoftAuthError
from ..auth.yggdrasil import YggdrasilAuth, YggdrasilError, AuthServer, ELYBY
from .net import Http
from .task import Reporter


class SessionError(Exception):
    """Не удалось обновить сессию аккаунта."""


def ensure_valid_account(
    account: Account,
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
) -> Account:
    """Обновляет токен, если он истёк. Возвращает актуальный аккаунт."""
    if account is None:
        raise SessionError("Аккаунт не выбран")

    if account.type == AccountType.OFFLINE:
        return account

    if not account.expired and account.access_token:
        return account

    if reporter:
        reporter.status(f"Обновление сессии «{account.name}»…")

    if account.type == AccountType.MICROSOFT:
        auth = MicrosoftAuth(http)
        try:
            fresh = auth.refresh(account)
        except MicrosoftAuthError as error:
            if error.needs_reauth:
                raise SessionError(
                    "Сессия Microsoft истекла. Войдите в аккаунт заново "
                    "на вкладке «Аккаунты»."
                ) from error
            raise SessionError(f"Не удалось обновить сессию: {error}") from error
        fresh.touch()
        return fresh

    if account.type == AccountType.ELYBY:
        server = _server_for(account)
        auth = YggdrasilAuth(http)
        try:
            fresh = auth.refresh(server, account)
        except YggdrasilError as error:
            if error.needs_two_factor or error.needs_captcha:
                raise SessionError(str(error)) from error
            raise SessionError(
                f"Не удалось обновить сессию {server.title}. Войдите заново."
            ) from error
        fresh.auth_server = server.base
        fresh.touch()
        return fresh

    return account


def _server_for(account: Account) -> AuthServer:
    for server in (ELYBY,):
        if account.auth_server and account.auth_server.rstrip("/") in server.base:
            return server
    if account.auth_server:
        return AuthServer(
            id="custom",
            title="Сервер авторизации",
            base=account.auth_server,
        )
    return ELYBY
