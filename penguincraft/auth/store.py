"""Хранилище аккаунтов (``accounts.json``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

from .account import Account, AccountType


class AccountStore:
    """Простое JSON-хранилище списка аккаунтов.

    Токены лежат в открытом виде — это норма для десктопных лаунчеров,
    но файл создаётся с правами ``600``.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._accounts: List[Account] = []
        self.active_id: str = ""
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        self._accounts = []
        self.active_id = ""
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.active_id = raw.get("active", "") or ""
        for item in raw.get("accounts", []):
            try:
                self._accounts.append(Account.from_dict(item))
            except (TypeError, ValueError):
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active": self.active_id,
            "accounts": [account.to_dict() for account in self._accounts],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    @property
    def accounts(self) -> List[Account]:
        return list(self._accounts)

    def __len__(self) -> int:
        return len(self._accounts)

    def __iter__(self) -> Iterable[Account]:
        return iter(self._accounts)

    def get(self, account_id: str) -> Optional[Account]:
        for account in self._accounts:
            if account.id == account_id:
                return account
        return None

    def find_by_name(self, name: str) -> Optional[Account]:
        lowered = name.lower()
        for account in self._accounts:
            if account.name.lower() == lowered:
                return account
        return None

    @property
    def active(self) -> Optional[Account]:
        if self.active_id:
            found = self.get(self.active_id)
            if found:
                return found
        return self._accounts[0] if self._accounts else None

    def set_active(self, account: Account | str) -> None:
        account_id = account if isinstance(account, str) else account.id
        if self.get(account_id):
            self.active_id = account_id
            self.save()

    # ------------------------------------------------------------------
    def add(self, account: Account, *, make_active: bool = True) -> Account:
        """Добавляет или обновляет аккаунт (по id)."""
        existing = self.get(account.id)
        if existing is not None:
            index = self._accounts.index(existing)
            self._accounts[index] = account
        else:
            self._accounts.append(account)
        if make_active or not self.active_id:
            self.active_id = account.id
        self.save()
        return account

    def update(self, account: Account) -> Account:
        existing = self.get(account.id)
        if existing is None:
            return self.add(account, make_active=False)
        index = self._accounts.index(existing)
        self._accounts[index] = account
        self.save()
        return account

    def remove(self, account: Account | str) -> bool:
        target = self.get(account if isinstance(account, str) else account.id)
        if not target:
            return False
        self._accounts.remove(target)
        if self.active_id == target.id:
            self.active_id = self._accounts[0].id if self._accounts else ""
        self.save()
        return True

    def by_type(self, account_type: str) -> List[Account]:
        return [account for account in self._accounts if account.type == account_type]

    def reorder(self, accounts: List[Account]) -> None:
        known = {account.id: account for account in self._accounts}
        ordered = [known[item.id] for item in accounts if item.id in known]
        for account in self._accounts:
            if account not in ordered:
                ordered.append(account)
        self._accounts = ordered
        self.save()
