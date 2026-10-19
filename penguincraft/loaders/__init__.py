"""Реестр загрузчиков модов."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from ..core.paths import Paths
from .base import Loader, LoaderError, LoaderVersion
from .fabric import FabricLoader, QuiltLoader
from .forge import ForgeLoader, NeoForgeLoader
from .vanilla import VanillaLoader

__all__ = [
    "Loader",
    "LoaderError",
    "LoaderVersion",
    "VanillaLoader",
    "FabricLoader",
    "QuiltLoader",
    "ForgeLoader",
    "NeoForgeLoader",
    "all_loaders",
    "get_loader",
    "loader_for_version",
    "local_versions",
]


def all_loaders() -> List[Loader]:
    """Все поддерживаемые загрузчики в порядке отображения."""
    return [
        VanillaLoader(),
        FabricLoader(),
        QuiltLoader(),
        ForgeLoader(),
        NeoForgeLoader(),
    ]


def get_loader(loader_id: str) -> Loader:
    for loader in all_loaders():
        if loader.id == loader_id:
            return loader
    raise KeyError(f"Неизвестный загрузчик: {loader_id}")


#: Порядок, в котором определяем загрузчик по id версии.
_DETECTION_ORDER = ("neoforge", "forge", "quilt", "fabric")


def loader_for_version(version_id: str, version_json: Optional[Mapping[str, object]] = None) -> Loader:
    """Определяет загрузчик по идентификатору версии."""
    lowered = version_id.lower()
    for loader_id in _DETECTION_ORDER:
        if loader_id in lowered:
            return get_loader(loader_id)
    if version_json is not None:
        main_class = str(version_json.get("mainClass") or "").lower()
        for loader_id in _DETECTION_ORDER:
            if loader_id in main_class:
                return get_loader(loader_id)
    return get_loader("vanilla")


class LocalVersion:
    """Локально установленная версия (то, что показывает список «Версии»)."""

    __slots__ = ("version_id", "loader_id", "game_version", "title", "installed_at", "has_client")

    def __init__(
        self,
        version_id: str,
        loader_id: str,
        game_version: str,
        title: str,
        installed_at: float = 0.0,
        has_client: bool = False,
    ) -> None:
        self.version_id = version_id
        self.loader_id = loader_id
        self.game_version = game_version
        self.title = title
        self.installed_at = installed_at
        self.has_client = has_client

    @property
    def loader_title(self) -> str:
        try:
            return get_loader(self.loader_id).title
        except KeyError:
            return self.loader_id

    def to_dict(self) -> Dict[str, object]:
        return {
            "version_id": self.version_id,
            "loader": self.loader_id,
            "game_version": self.game_version,
            "title": self.title,
            "installed_at": self.installed_at,
            "has_client": self.has_client,
        }


def _game_version_of(version: Mapping[str, object]) -> str:
    inherits = version.get("inheritsFrom")
    if inherits:
        return str(inherits)
    version_id = str(version.get("id") or "")
    loader = loader_for_version(version_id, version)
    if loader.id != "vanilla":
        part = version_id.split("-")[-1]
        return part
    return version_id


def local_versions(paths: Paths) -> List[LocalVersion]:
    """Список установленных версий с определением загрузчика."""
    result: List[LocalVersion] = []
    if not paths.versions.is_dir():
        return result
    for entry in sorted(paths.versions.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        json_path = entry / f"{entry.name}.json"
        if not json_path.is_file():
            continue
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        loader = loader_for_version(entry.name, raw)
        client_exists = any(
            candidate.is_file()
            for candidate in entry.glob("*.jar")
        )
        if not client_exists:
            # клиент может лежать под родительской версией
            jar_id = str(raw.get("jar") or "")
            if jar_id:
                client_exists = (paths.versions / jar_id / f"{jar_id}.jar").is_file()
        installed_at = 0.0
        try:
            installed_at = json_path.stat().st_mtime
        except OSError:
            pass
        result.append(
            LocalVersion(
                version_id=entry.name,
                loader_id=loader.id,
                game_version=_game_version_of(raw),
                title=entry.name,
                installed_at=installed_at,
                has_client=client_exists,
            )
        )
    return result
