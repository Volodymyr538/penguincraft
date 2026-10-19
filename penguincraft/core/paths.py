"""Пути, используемые лаунчером.

Структура каталога данных (по умолчанию ``~/.local/share/penguincraft``
в Linux/ChromeOS и ``%LOCALAPPDATA%\\PenguinCraft`` в Windows)::

    <root>/
        settings.json          настройки
        accounts.json          аккаунты
        meta/                  кеш метаданных (манифест версий и т.п.)
        versions/<id>/<id>.json    описания версий (в т.ч. Forge/Fabric)
        libraries/             общие библиотеки (maven-раскладка)
        assets/
            indexes/<id>.json
            objects/<ab>/<hash>
            virtual/legacy/... (для очень старых версий)
        runtimes/<major>/      автоматически скачанные JRE
        instances/<имя>/       отдельный экземпляр игры
            .minecraft/        то, что видит Minecraft как gameDirectory
                mods/          моды (для Fabric/Forge/…)
                saves/         миры
                config/        настройки модов
                resourcepacks/
                shaderpacks/
            instance.json      метаданные экземпляра
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from .. import APP_ID, APP_NAME
from .util import is_windows

#: Переменная окружения, переопределяющая каталог данных лаунчера.
ENV_ROOT = "PENGUINCRAFT_HOME"

#: Имя файла настроек.
SETTINGS_FILE = "settings.json"
#: Имя файла аккаунтов.
ACCOUNTS_FILE = "accounts.json"

#: Как называется «домашний» каталог игры внутри экземпляра.
#: ``.minecraft`` — это то самое имя, которое знает любой игрок: так
#: называлась папка у официального лаунчера (``%APPDATA%\.minecraft``,
#: ``~/.minecraft``) и так же поступают Prism/MultiMC, отводя каждому
#: экземпляру собственный ``.minecraft``.
GAME_DIR = ".minecraft"

#: Прежнее имя того же каталога (до 1.4.0) — нужно для переноса данных.
LEGACY_GAME_DIR = "game"


def default_root() -> Path:
    """Каталог данных лаунчера по умолчанию.

    * Linux/ChromeOS (Crostini) — ``~/.local/share/penguincraft``
      (уважает ``XDG_DATA_HOME``), доступно без root.
    * Windows — ``%LOCALAPPDATA%\\PenguinCraft`` (а не роуминг-профиль:
      ресурсы и библиотеки игры весят гигабайты и не должны попадать
      в синхронизацию).
    """
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser()
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        base = Path(xdg).expanduser()
    else:
        base = Path.home() / ".local" / "share"
    return base / APP_ID


class Paths:
    """Набор путей лаунчера с ленивым созданием каталогов."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_root()

    # ---- корневые каталоги -------------------------------------------------
    @property
    def settings(self) -> Path:
        return self.root / SETTINGS_FILE

    @property
    def accounts(self) -> Path:
        return self.root / ACCOUNTS_FILE

    @property
    def meta(self) -> Path:
        return self.root / "meta"

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def libraries(self) -> Path:
        return self.root / "libraries"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def asset_indexes(self) -> Path:
        return self.assets / "indexes"

    @property
    def asset_objects(self) -> Path:
        return self.assets / "objects"

    @property
    def runtimes(self) -> Path:
        return self.root / "runtimes"

    @property
    def instances(self) -> Path:
        return self.root / "instances"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def downloads(self) -> Path:
        """Каталог для временных загрузок (установщики, .mrpack)."""
        return self.cache / "downloads"

    # ---- экземпляры --------------------------------------------------------
    def instance_dir(self, name: str) -> Path:
        return self.instances / name

    def instance_game_dir(self, name: str) -> Path:
        """Каталог игры экземпляра — привычный ``.minecraft``."""
        return self.instance_dir(name) / GAME_DIR

    def instance_legacy_game_dir(self, name: str) -> Path:
        """Прежнее имя каталога игры (``game``) — только для переноса."""
        return self.instance_dir(name) / LEGACY_GAME_DIR

    def migrate_legacy_game_dirs(self) -> List[str]:
        """Переименовывает ``game`` -> ``.minecraft`` в старых экземплярах.

        Возвращает имена перенесённых экземпляров.  Если ``.minecraft`` уже
        есть, каталог не трогаем — данные пользователя важнее.
        """
        renamed: List[str] = []
        if not self.instances.is_dir():
            return renamed
        for entry in sorted(self.instances.iterdir()):
            if not entry.is_dir():
                continue
            legacy = entry / LEGACY_GAME_DIR
            current = entry / GAME_DIR
            if current.exists() or not legacy.is_dir():
                continue
            try:
                legacy.rename(current)
            except OSError:
                continue
            renamed.append(entry.name)
        return renamed

    def instance_json(self, name: str) -> Path:
        return self.instance_dir(name) / "instance.json"

    def instance_index(self, name: str) -> Path:
        """Индекс установленных с Modrinth проектов (для проверки обновлений)."""
        return self.instance_dir(name) / "installed.json"

    def version_json(self, version_id: str) -> Path:
        return self.versions / version_id / f"{version_id}.json"

    def version_dir(self, version_id: str) -> Path:
        return self.versions / version_id

    def natives_dir(self, version_id: str) -> Path:
        # natives только распаковываются, поэтому держим их рядом с версией.
        return self.versions / version_id / "natives"

    def runtime_dir(self, major: int) -> Path:
        return self.runtimes / f"jre-{major}"

    # ---- создание ----------------------------------------------------------
    def ensure_base_dirs(self) -> None:
        for path in (
            self.root,
            self.meta,
            self.versions,
            self.libraries,
            self.asset_indexes,
            self.asset_objects,
            self.runtimes,
            self.instances,
            self.logs,
            self.downloads,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def ensure_instance(self, name: str) -> Path:
        """Создаёт каталог экземпляра и стандартные подкаталоги игры."""
        game = self.instance_game_dir(name)
        for sub in ("mods", "config", "resourcepacks", "shaderpacks", "saves", ""):
            (game / sub).mkdir(parents=True, exist_ok=True)
        return game


def natives_for_os() -> str:
    """Возвращает ``name`` текущей ОС в терминах манифеста Mojang."""
    import platform

    system = platform.system().lower()
    if system == "darwin":
        return "osx"
    if system == "windows":
        return "windows"
    return "linux"
