"""Fabric и Quilt — загрузчики с общим meta-API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ..core.mojang import VersionRepository
from ..core.net import Http
from ..core.paths import Paths
from ..core.settings import Settings
from ..core.task import Reporter
from .base import Loader, LoaderError, LoaderVersion, _version_tuple


class FabricLikeLoader(Loader):
    """Базовый класс для загрузчиков, публикующих готовый profile json."""

    #: Базовый URL meta-API (без завершающего слэша).
    meta_base: str = ""
    #: Идентификатор в id версии («fabric-loader-…», «quilt-loader-…»).
    id_prefix: str = ""

    # ------------------------------------------------------------------
    def _url(self, *parts: str) -> str:
        return "/".join([self.meta_base.rstrip("/"), *[str(p).strip("/") for p in parts]])

    def list_versions(
        self,
        paths: Paths,
        http: Http,
        game_version: str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> List[LoaderVersion]:
        if reporter:
            reporter.status(f"Получение версий {self.title} для {game_version}…")
        try:
            data = http.get_json(self._url("v2", "versions", "loader", game_version) if "fabric" in self.meta_base else self._url("v3", "versions", "loader", game_version))
        except Exception as error:  # noqa: BLE001
            raise LoaderError(
                f"Не удалось получить список версий {self.title}: {error}"
            ) from error
        result: List[LoaderVersion] = []
        for entry in data or []:
            loader = entry.get("loader") or {}
            version = str(loader.get("version") or "")
            if not version:
                continue
            build = int(loader.get("build") or 0)
            stable = bool(loader.get("stable", True))
            result.append(
                LoaderVersion(
                    version=version,
                    game_version=game_version,
                    stable=stable,
                    build=build,
                )
            )
        result.sort(key=lambda item: item.sort_key, reverse=True)
        if result:
            result[0].label = "последняя"
            for item in result:
                if item.stable:
                    if item is not result[0]:
                        item.note = "стабильная"
                    break
        return result

    # ------------------------------------------------------------------
    def profile_url(self, game_version: str, loader_version: str) -> str:
        if "quilt" in self.meta_base:
            return self._url("v3", "versions", "loader", game_version, loader_version, "profile", "json")
        return self._url("v2", "versions", "loader", game_version, loader_version, "profile", "json")

    def fetch_profile(
        self,
        http: Http,
        game_version: str,
        loader_version: str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> Dict[str, Any]:
        if reporter:
            reporter.status(f"Скачивание профиля {self.title} {loader_version}…")
        try:
            profile = http.get_json(self.profile_url(game_version, loader_version))
        except Exception as error:  # noqa: BLE001
            raise LoaderError(f"Не удалось скачать профиль {self.title}: {error}") from error
        if not isinstance(profile, dict) or not profile.get("id"):
            raise LoaderError(f"{self.title} вернул некорректный профиль загрузчика")
        return profile

    # ------------------------------------------------------------------
    def install(
        self,
        paths: Paths,
        settings: Settings,
        http: Http,
        game_version: str,
        loader_version: str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> str:
        paths.ensure_base_dirs()
        profile = self.fetch_profile(http, game_version, loader_version, reporter=reporter)
        version_id = str(profile["id"])
        target = paths.version_json(version_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        if reporter:
            reporter.log(f"{self.title} {loader_version}: профиль сохранён как {version_id}")
        return version_id

    def matches(self, version_json: Mapping[str, object]) -> bool:
        version_id = str(version_json.get("id") or "")
        main_class = str(version_json.get("mainClass") or "")
        return self.id_prefix in version_id or self.id.lower() in main_class.lower()


class FabricLoader(FabricLikeLoader):
    def __init__(self) -> None:
        super().__init__(
            id="fabric",
            title="Fabric",
            description=(
                "Лёгкий и быстрый загрузчик модов. Идеален для хромбуков: "
                "минимум накладных расходов, огромный выбор модов на Modrinth."
            ),
            website="https://fabricmc.net",
            color="#dbb69a",
        )
        self.meta_base = "https://meta.fabricmc.net"
        self.id_prefix = "fabric-loader-"


class QuiltLoader(FabricLikeLoader):
    def __init__(self) -> None:
        super().__init__(
            id="quilt",
            title="Quilt",
            description=(
                "Форк Fabric с более гибким API. Совместим с большинством "
                "Fabric-модов."
            ),
            website="https://quiltmc.org",
            color="#a56bd6",
        )
        self.meta_base = "https://meta.quiltmc.org"
        self.id_prefix = "quilt-loader-"
