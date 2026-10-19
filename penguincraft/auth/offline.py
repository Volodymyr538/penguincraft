"""Офлайн-аккаунты.

Используются для игры на серверах с ``online-mode=false``, для тестов
модов и просто чтобы не заводить пароль.  UUID считается по той же
схеме, что и у серверов: MD5 от ``OfflinePlayer:<ник>`` с версией 3.
"""

from __future__ import annotations

import re
import time

from .account import Account, AccountType, offline_uuid

#: Ники в Minecraft: 3–16 символов, латиница/цифры/подчёркивание.
NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")

#: Зарезервированные слова, которые игра не примет.
RESERVED = {
    "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
}


class InvalidName(ValueError):
    """Ник не подходит под правила Minecraft."""


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise InvalidName(
            "Ник должен содержать от 3 до 16 символов: латинские буквы, "
            "цифры и подчёркивание."
        )
    if name.lower() in RESERVED:
        raise InvalidName("Такое имя использовать нельзя.")
    return name


def make_offline_account(name: str) -> Account:
    """Создаёт офлайн-аккаунт с корректным UUID."""
    clean = validate_name(name)
    return Account(
        type=AccountType.OFFLINE,
        name=clean,
        uuid=offline_uuid(clean),
        access_token="0",
        user_type="legacy",
        created_at=time.time(),
        last_used=time.time(),
    )
