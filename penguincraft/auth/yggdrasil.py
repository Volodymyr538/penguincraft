"""Авторизация через Yggdrasil-совместимые серверы (Ely.by и другие).

Ely.by — популярный русскоязычный auth-сервер, поддерживающий пиратские
и лицензионные профили.  Протокол совместим с ``authserver.mojang.com``
времён 1.6+, поэтому модуль годится и для своих серверов авторизации.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..core.net import Http, HttpError
from .account import Account, AccountType


@dataclass(frozen=True)
class AuthServer:
    """Описание auth-сервера."""

    id: str
    title: str
    base: str
    website: str = ""
    note: str = ""

    @property
    def authenticate_url(self) -> str:
        return f"{self.base.rstrip('/')}/auth/authenticate"

    @property
    def refresh_url(self) -> str:
        return f"{self.base.rstrip('/')}/auth/refresh"

    @property
    def validate_url(self) -> str:
        return f"{self.base.rstrip('/')}/auth/validate"

    @property
    def invalidate_url(self) -> str:
        return f"{self.base.rstrip('/')}/auth/invalidate"

    @property
    def signout_url(self) -> str:
        return f"{self.base.rstrip('/')}/auth/signout"


ELYBY = AuthServer(
    id="elyby",
    title="Ely.by",
    base="https://authserver.ely.by",
    website="https://ely.by",
    note=(
        "Русскоязычный сервис: вход по логину и паролю, поддерживает "
        "скины и плащи. Подходит и для лицензии, и для сторонних лаунчеров."
    ),
)

MOJANG_LEGACY = AuthServer(
    id="mojang",
    title="Mojang (устаревший)",
    base="https://authserver.mojang.com",
    website="https://account.mojang.com",
    note="Старый протокол Mojang. С 2023 года новые аккаунты не работают.",
)

AUTH_SERVERS = [ELYBY, MOJANG_LEGACY]


class YggdrasilError(Exception):
    """Ошибка авторизации. ``code`` — короткий идентификатор ошибки."""

    def __init__(self, message: str, code: str = "unknown", payload: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload

    @property
    def needs_two_factor(self) -> bool:
        return self.code in ("twofactor", "auth.twofactor")

    @property
    def needs_captcha(self) -> bool:
        return self.code == "captcha"


def _friendly_error(payload: Any, status: int) -> YggdrasilError:
    if isinstance(payload, dict):
        code = str(payload.get("error") or "").lower()
        message = str(payload.get("errorMessage") or payload.get("message") or "")
        if payload.get("error") in ("CredentialsExpired",):
            return YggdrasilError("Сессия устарела, войдите заново.", "credentials_expired", payload)
        if code in ("forbiddenoperationexception",):
            lowered = message.lower()
            if "invalid credentials" in lowered or "пароль" in lowered or "password" in lowered:
                return YggdrasilError("Неверный логин или пароль.", "invalid_credentials", payload)
            if "two factor" in lowered or "two-factor" in lowered:
                return YggdrasilError(
                    "Требуется двухфакторная аутентификация. Войдите через сайт и отключите её "
                    "или используйте офлайн-профиль.",
                    "twofactor",
                    payload,
                )
            if "captcha" in lowered:
                return YggdrasilError(
                    "Сервер требует капчу. Зайдите на сайт сервиса и подтвердите вход.",
                    "captcha",
                    payload,
                )
            return YggdrasilError(message or "Доступ запрещён.", "forbidden", payload)
        if message:
            return YggdrasilError(message, code or "error", payload)
    if status == 401:
        return YggdrasilError("Неверный логин или пароль.", "invalid_credentials", payload)
    if status >= 500:
        return YggdrasilError("Сервер авторизации недоступен. Попробуйте позже.", "server", payload)
    return YggdrasilError(f"Ошибка авторизации (HTTP {status}).", "http", payload)


class YggdrasilAuth:
    """Клиент протокола Yggdrasil."""

    def __init__(self, http: Optional[Http] = None) -> None:
        self.http = http or Http()

    # -- низкий уровень -----------------------------------------------------
    def _call(self, url: str, payload: Dict[str, Any]) -> Any:
        try:
            return self.http.post_json(url, payload)
        except HttpError as error:
            decoded: Any = None
            try:
                import json

                decoded = json.loads(error.body) if error.body else None
            except Exception:  # noqa: BLE001
                decoded = error.body
            raise _friendly_error(decoded, error.status) from error

    # -- операции -----------------------------------------------------------
    def authenticate(
        self,
        server: AuthServer,
        username: str,
        password: str,
        *,
        client_token: str = "",
    ) -> Account:
        """Вход по логину и паролю.

        ``username`` может быть ником или e-mail.
        """
        client_token = client_token or str(uuid.uuid4())
        payload = {
            "username": username.strip(),
            "password": password,
            "clientToken": client_token,
            "requestUser": True,
        }
        response = self._call(server.authenticate_url, payload)
        return self._account_from_response(server, response, client_token)

    def refresh(self, server: AuthServer, account: Account) -> Account:
        """Обновляет access token по refresh-токену."""
        if not account.access_token or not account.client_token:
            raise YggdrasilError("Нет данных для обновления сессии.", "no_refresh_token")
        payload = {
            "accessToken": account.access_token,
            "clientToken": account.client_token,
            "requestUser": True,
        }
        try:
            response = self._call(server.refresh_url, payload)
        except YggdrasilError:
            raise
        updated = self._account_from_response(server, response, account.client_token)
        updated.refresh_token = account.refresh_token
        return updated

    def validate(self, server: AuthServer, account: Account) -> bool:
        try:
            self._call(
                server.validate_url,
                {"accessToken": account.access_token, "clientToken": account.client_token},
            )
            return True
        except YggdrasilError:
            return False

    def invalidate(self, server: AuthServer, account: Account) -> None:
        try:
            self._call(
                server.invalidate_url,
                {"accessToken": account.access_token, "clientToken": account.client_token},
            )
        except YggdrasilError:
            pass

    # -- преобразование -----------------------------------------------------
    def _account_from_response(
        self, server: AuthServer, response: Any, client_token: str
    ) -> Account:
        if not isinstance(response, dict):
            raise YggdrasilError("Неожиданный ответ сервера авторизации.", "bad_response", response)
        access_token = response.get("accessToken") or ""
        profile = response.get("selectedProfile") or {}
        profiles = response.get("availableProfiles") or []
        if not profile and profiles:
            profile = profiles[0]
        if not access_token:
            raise YggdrasilError("Сервер не выдал токен доступа.", "no_token", response)
        if not profile:
            raise YggdrasilError(
                "У аккаунта нет игрового профиля. Зайдите на сайт сервиса и создайте персонажа.",
                "no_profile",
                response,
            )
        user = response.get("user") or {}
        return Account(
            type=AccountType.ELYBY if server.id == "elyby" else AccountType.ELYBY,
            name=str(profile.get("name") or ""),
            uuid=str(profile.get("id") or ""),
            access_token=str(access_token),
            client_token=str(response.get("clientToken") or client_token),
            user_type="mojang",
            auth_server=server.base,
            expires_at=time.time() + 7 * 24 * 3600,  # Yggdrasil-токены живут долго
            created_at=time.time(),
            last_used=time.time(),
        )
