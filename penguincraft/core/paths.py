"""Пути, используемые лаунчером.

Структура каталога данных (по умолчанию ``~/.local/share/penguincraft``)::

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
        instances/<name>/      отдельный "домашний" каталог игры
            game/              то, что видит Minecraft как gameDirectory
            instance.json      метаданные экземпляра
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import APP_ID

#: Переменная окружения, переопределяющая каталог данных лаунчера.
ENV_ROOT = "PENGUINCRAFT_HOME"

#: Имя файла настроек.
SETTINGS_FILE = "settings.json"
#: Имя файла аккаунтов.
ACCOUNTS_FILE = "accounts.json"


def default_root() -> Path:
    """Каталог данных лаунчера по умолчанию.

    На Chromebook'ах (Crostini) это место доступно и не требует root.
    """
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser()
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
        return self.instance_dir(name) / "game"

    def instance_json(self, name: str) -> Path:
        return self.instance_dir(name) / "instance.json"

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
