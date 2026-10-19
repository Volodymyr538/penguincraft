"""Авторизация Microsoft/Xbox Live (device code flow).

Схема полностью повторяет официальный лаунчер:

1. получаем код устройства и показываем пользователю ``microsoft.com/link``;
2. опрашиваем токен-эндпоинт, пока пользователь вводит код;
3. меняем Microsoft-токен на Xbox Live-токен;
4. получаем XSTS-токен;
5. меняем его на токен Minecraft;
6. забираем профиль игрока (ник и UUID).

Никаких паролей лаунчер не видит — вход происходит в браузере, что
удобно на хромбуках (и безопаснее).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ..core.net import Http, HttpError
from ..core.task import Cancelled, Reporter
from .account import Account, AccountType

#: Публичный client_id приложения «Minecraft Launcher».
DEFAULT_CLIENT_ID = "00000000402b5328"
DEVICE_CODE_SCOPE = "XboxLive.signin offline_access"

DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"

XSTS_MESSAGES = {
    "2148916233": "У этой учётной записи Microsoft нет профиля Xbox. "
                  "Создайте его на xbox.com и попробуйте снова.",
    "2148916235": "Xbox Live недоступен в вашем регионе.",
    "2148916236": "Требуется подтверждение возраста в настройках Microsoft.",
    "2148916237": "Требуется подтверждение возраста в настройках Microsoft.",
    "2148916238": "Детская учётная запись: добавьте её в семью и разрешите "
                  "доступ к Minecraft.",
}


class MicrosoftAuthError(Exception):
    """Ошибка входа через Microsoft."""

    def __init__(self, message: str, code: str = "error", payload: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload

    @property
    def needs_reauth(self) -> bool:
        return self.code in ("invalid_grant", "unauthorized_client", "no_refresh_token")


@dataclass
class DeviceCodePrompt:
    """Данные, которые надо показать пользователю."""

    user_code: str
    verification_uri: str
    device_code: str
    interval: int
    expires_in: int
    message: str = ""

    @property
    def short_uri(self) -> str:
        return (
            self.verification_uri
            .replace("https://", "")
            .replace("http://", "")
            .rstrip("/")
        )


class MicrosoftAuth:
    """Клиент цепочки Microsoft → Xbox Live → Minecraft."""

    def __init__(self, http: Optional[Http] = None, client_id: str = DEFAULT_CLIENT_ID) -> None:
        self.http = http or Http()
        self.client_id = client_id

    # ------------------------------------------------------------------
    # Шаг 1. Код устройства
    # ------------------------------------------------------------------
    def request_device_code(self) -> DeviceCodePrompt:
        try:
            payload = self.http.post_form(
                DEVICE_CODE_URL,
                {"client_id": self.client_id, "scope": DEVICE_CODE_SCOPE},
            )
        except HttpError as error:
            raise MicrosoftAuthError(
                "Не удалось начать вход в Microsoft. Проверьте интернет.",
                "devicecode_failed",
                error.body,
            ) from error
        if not isinstance(payload, dict) or "device_code" not in payload:
            raise MicrosoftAuthError("Microsoft вернул неожиданный ответ.", "bad_response", payload)
        return DeviceCodePrompt(
            user_code=str(payload.get("user_code", "")),
            verification_uri=str(payload.get("verification_uri", "https://microsoft.com/link")),
            device_code=str(payload["device_code"]),
            interval=int(payload.get("interval", 5) or 5),
            expires_in=int(payload.get("expires_in", 900) or 900),
            message=str(payload.get("message", "")),
        )

    # ------------------------------------------------------------------
    # Шаг 2. Ожидание входа
    # ------------------------------------------------------------------
    def wait_for_microsoft_token(
        self,
        prompt: DeviceCodePrompt,
        *,
        reporter: Optional[Reporter] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + prompt.expires_in
        interval = max(2, prompt.interval)
        while True:
            if reporter:
                reporter.check_cancel()
            if time.monotonic() > deadline:
                raise MicrosoftAuthError(
                    "Время ожидания входа истекло. Попробуйте снова.", "expired_token"
                )
            if on_status:
                remaining = int(max(0, deadline - time.monotonic()))
                on_status(
                    f"Откройте {prompt.short_uri} и введите код {prompt.user_code} "
                    f"(осталось {remaining // 60}:{remaining % 60:02d})"
                )
            try:
                payload = self.http.post_form(
                    TOKEN_URL,
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": self.client_id,
                        "device_code": prompt.device_code,
                    },
                )
            except HttpError as error:
                payload = _decode_error(error)
                code = str((payload or {}).get("error") or "")
                if code == "authorization_pending":
                    time.sleep(interval)
                    continue
                if code == "slow_down":
                    interval += 5
                    time.sleep(interval)
                    continue
                if code == "authorization_declined":
                    raise MicrosoftAuthError("Вход отклонён пользователем.", "declined", payload) from error
                if code == "expired_token":
                    raise MicrosoftAuthError("Код устройства устарел.", "expired_token", payload) from error
                if code == "bad_verification_code":
                    raise MicrosoftAuthError("Неверный код устройства.", "bad_code", payload) from error
                raise MicrosoftAuthError(
                    _token_error_message(payload, error.status), code or "token_error", payload
                ) from error
            except (Cancelled, KeyboardInterrupt):
                raise
            if isinstance(payload, dict) and payload.get("access_token"):
                return payload
            # На всякий случай: сервер вернул 200 без токена
            time.sleep(interval)

    # ------------------------------------------------------------------
    # Шаг 3-6. Полная цепочка
    # ------------------------------------------------------------------
    def authenticate(
        self,
        *,
        reporter: Optional[Reporter] = None,
        on_prompt: Optional[Callable[[DeviceCodePrompt], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        verify_ownership: bool = True,
    ) -> Account:
        prompt = self.request_device_code()
        if on_prompt:
            on_prompt(prompt)
        if reporter:
            reporter.status(
                f"Введите код {prompt.user_code} на сайте {prompt.short_uri}"
            )
        token = self.wait_for_microsoft_token(prompt, reporter=reporter, on_status=on_status)
        return self.login_with_microsoft_token(
            token, reporter=reporter, on_status=on_status, verify_ownership=verify_ownership
        )

    def login_with_microsoft_token(
        self,
        token: Dict[str, Any],
        *,
        reporter: Optional[Reporter] = None,
        on_status: Optional[Callable[[str], None]] = None,
        verify_ownership: bool = True,
    ) -> Account:
        def status(text: str) -> None:
            if on_status:
                on_status(text)
            if reporter:
                reporter.status(text)

        status("Подключение к Xbox Live…")
        xbl = self._xbox_live(token["access_token"])
        status("Получение разрешения XSTS…")
        xsts = self._xsts(xbl)
        status("Вход в Minecraft…")
        mc = self._minecraft_login(xsts)
        status("Загрузка профиля игрока…")
        profile = self._minecraft_profile(mc["access_token"])

        name = str(profile.get("name") or "")
        uuid = str(profile.get("id") or "")
        if not name or not uuid:
            raise MicrosoftAuthError(
                "Не удалось получить профиль Minecraft. Убедитесь, что игра куплена "
                "на этой учётной записи.",
                "no_profile",
                profile,
            )
        skins = profile.get("skins") or []
        skin_model = "classic"
        for skin in skins:
            if skin.get("state") == "ACTIVE":
                skin_model = str(skin.get("variant") or "classic").lower()
                break

        if verify_ownership:
            owned = self._check_entitlements(mc["access_token"])
            if owned is False:
                status(
                    "Внимание: на этом аккаунте не найдена лицензия Minecraft. "
                    "Игра может запуститься, только если лицензия есть в наличии."
                )

        expires_in = int(mc.get("expires_in", 86400) or 86400)
        return Account(
            type=AccountType.MICROSOFT,
            name=name,
            uuid=uuid,
            access_token=str(mc["access_token"]),
            refresh_token=str(token.get("refresh_token", "")),
            client_token=self.client_id,
            xuid=str(xbl.get("xuid", "")),
            user_type="msa",
            skin_model=skin_model,
            expires_at=time.time() + expires_in,
            created_at=time.time(),
            last_used=time.time(),
        )

    # ------------------------------------------------------------------
    # Обновление
    # ------------------------------------------------------------------
    def refresh(self, account: Account) -> Account:
        """Обновляет истёкший аккаунт Microsoft."""
        if not account.refresh_token:
            raise MicrosoftAuthError(
                "Нет refresh-токена, войдите в аккаунт заново.", "no_refresh_token"
            )
        try:
            token = self.http.post_form(
                TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "refresh_token": account.refresh_token,
                    "scope": DEVICE_CODE_SCOPE,
                },
            )
        except HttpError as error:
            payload = _decode_error(error)
            code = str((payload or {}).get("error") or "refresh_failed")
            if code in ("invalid_grant", "unauthorized_client"):
                raise MicrosoftAuthError(
                    "Сессия Microsoft истекла, войдите заново.", "invalid_grant", payload
                ) from error
            raise MicrosoftAuthError(
                _token_error_message(payload, error.status), code, payload
            ) from error
        if not isinstance(token, dict) or not token.get("access_token"):
            raise MicrosoftAuthError("Не удалось обновить сессию.", "bad_response", token)
        fresh = self.login_with_microsoft_token(token, verify_ownership=False)
        fresh.refresh_token = str(token.get("refresh_token") or account.refresh_token)
        fresh.created_at = account.created_at
        return fresh

    # ------------------------------------------------------------------
    # Внутренние шаги цепочки
    # ------------------------------------------------------------------
    def _xbox_live(self, microsoft_token: str) -> Dict[str, str]:
        payload = {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": microsoft_token,
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        }
        data = self.http.post_json(
            XBL_URL,
            payload,
            headers={"Accept": "application/json", "x-xbl-contract-version": "1"},
        )
        if not isinstance(data, dict) or "Token" not in data:
            raise MicrosoftAuthError("Xbox Live отклонил вход.", "xbl_failed", data)
        claims = (data.get("DisplayClaims") or {}).get("xui") or [{}]
        return {"token": str(data["Token"]), "uhs": str(claims[0].get("uhs", ""))}

    def _xsts(self, xbl: Dict[str, str]) -> Dict[str, str]:
        payload = {
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl["token"]],
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        }
        try:
            data = self.http.post_json(
                XSTS_URL,
                payload,
                headers={"Accept": "application/json", "x-xbl-contract-version": "1"},
            )
        except HttpError as error:
            payload_error = _decode_error(error) or {}
            xerr = str(payload_error.get("XErr") or "")
            message = XSTS_MESSAGES.get(xerr)
            if not message:
                message = (
                    "Xbox Live не выдал разрешение (XErr=" + (xerr or "неизвестно") + "). "
                    "Проверьте, что у учётной записи есть профиль Xbox Live."
                )
            raise MicrosoftAuthError(message, "xsts_failed", payload_error) from error
        if not isinstance(data, dict) or "Token" not in data:
            raise MicrosoftAuthError("Xbox Live не выдал разрешение.", "xsts_failed", data)
        claims = (data.get("DisplayClaims") or {}).get("xui") or [{}]
        return {"token": str(data["Token"]), "uhs": str(claims[0].get("uhs", ""))}

    def _minecraft_login(self, xsts: Dict[str, str]) -> Dict[str, Any]:
        identity = f"XBL3.0 x={xsts['uhs']};{xsts['token']}"
        try:
            data = self.http.post_json(
                MC_LOGIN_URL,
                {"identityToken": identity},
                headers={"Accept": "application/json"},
            )
        except HttpError as error:
            raise MicrosoftAuthError(
                "Minecraft отклонил вход. Убедитесь, что игра куплена на этом аккаунте.",
                "mc_login_failed",
                _decode_error(error),
            ) from error
        if not isinstance(data, dict) or not data.get("access_token"):
            raise MicrosoftAuthError("Minecraft не выдал токен.", "mc_login_failed", data)
        return data

    def _minecraft_profile(self, access_token: str) -> Dict[str, Any]:
        try:
            data = self.http.get_json(
                MC_PROFILE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except HttpError as error:
            if error.status == 404:
                raise MicrosoftAuthError(
                    "У аккаунта нет профиля Minecraft — игра не куплена "
                    "или ник ещё не выбран.",
                    "no_profile",
                    error.body,
                ) from error
            if error.status == 401:
                raise MicrosoftAuthError("Токен Minecraft недействителен.", "mc_unauthorized") from error
            raise
        if not isinstance(data, dict):
            raise MicrosoftAuthError("Некорректный ответ профиля Minecraft.", "bad_response", data)
        return data

    def _check_entitlements(self, access_token: str) -> Optional[bool]:
        """True — лицензия есть, False — нет, None — не удалось проверить."""
        try:
            data = self.http.get_json(
                MC_ENTITLEMENTS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except Exception:  # noqa: BLE001 - проверка не критична
            return None
        if not isinstance(data, dict):
            return None
        items = data.get("items") or []
        return bool(items)


def _decode_error(error: HttpError) -> Any:
    import json

    if not error.body:
        return {}
    try:
        return json.loads(error.body)
    except json.JSONDecodeError:
        return {"error": "http_error", "errorMessage": error.body[:400]}


def _token_error_message(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        description = payload.get("error_description") or payload.get("errorMessage")
        if description:
            # Microsoft добавляет технические коды в скобках — уберём их.
            text = str(description).split("\r\n")[0].split("Trace ID")[0].strip()
            return text or "Ошибка входа в Microsoft."
    return f"Не удалось получить токен (HTTP {status})."
