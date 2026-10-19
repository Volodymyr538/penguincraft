"""Модель аккаунта и офлайн-UUID."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict


class AccountType:
    MICROSOFT = "microsoft"
    OFFLINE = "offline"
    ELYBY = "elyby"

    ALL = (MICROSOFT, OFFLINE, ELYBY)

    TITLES = {
        MICROSOFT: "Microsoft",
        OFFLINE: "Офлайн",
        ELYBY: "Ely.by",
    }


def offline_uuid(name: str) -> str:
    """UUID офлайн-игрока (совпадает с поведением серверов offline-mode)."""
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


def _dashed(value: str) -> str:
    value = (value or "").replace("-", "")
    if len(value) == 32:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            pass
    return value


@dataclass
class Account:
    """Один профиль игрока."""

    type: str = AccountType.OFFLINE
    name: str = ""
    uuid: str = ""
    access_token: str = ""
    refresh_token: str = ""
    client_token: str = ""
    xuid: str = ""
    user_type: str = "msa"          # msa | mojang | legacy
    expires_at: float = 0.0          # unix-время истечения access_token
    skin_model: str = "classic"
    auth_server: str = ""            # для yggdrasil-совместимых серверов
    created_at: float = field(default_factory=time.time)
    last_used: float = 0.0

    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        """Стабильный идентификатор записи в файле аккаунтов."""
        base = f"{self.type}:{self.auth_server}:{self.uuid or self.name}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    @property
    def display(self) -> str:
        return self.name or "(без имени)"

    @property
    def type_title(self) -> str:
        return AccountType.TITLES.get(self.type, self.type)

    @property
    def expired(self) -> bool:
        if not self.expires_at:
            return self.type == AccountType.MICROSOFT and not self.access_token
        return time.time() > (self.expires_at - 60)

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token)

    def touch(self) -> None:
        self.last_used = time.time()

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Account":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        payload = {k: v for k, v in raw.items() if k in known}
        account = cls(**payload)  # type: ignore[arg-type]
        account.uuid = _dashed(account.uuid)
        return account

    # ------------------------------------------------------------------
    # Поля для командной строки игры
    # ------------------------------------------------------------------
    def launch_identity(self) -> Dict[str, str]:
        """Значения подстановок ``${auth_*}``."""
        user_type = self.user_type
        if self.type == AccountType.OFFLINE:
            user_type = "legacy"
        elif self.type == AccountType.ELYBY and not user_type:
            user_type = "mojang"
        return {
            "auth_player_name": self.name,
            "auth_uuid": _dashed(self.uuid) if "-" in _dashed(self.uuid) else _dashed(self.uuid),
            "auth_access_token": self.access_token or "0",
            "auth_session": self.access_token or "0",
            "auth_xuid": self.xuid or "",
            "user_type": user_type,
            "user_properties": json.dumps({}),
            "clientid": self.client_token or "",
        }
