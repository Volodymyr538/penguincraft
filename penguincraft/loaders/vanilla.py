"""Ванильная версия Minecraft (без загрузчиков)."""

from __future__ import annotations

from typing import List, Mapping, Optional

from ..core.mojang import VersionManifest, VersionRepository
from ..core.net import Http
from ..core.paths import Paths
from ..core.settings import Settings
from ..core.task import Reporter
from .base import Loader, LoaderVersion


class VanillaLoader(Loader):
    def __init__(self) -> None:
        super().__init__(
            id="vanilla",
            title="Без модов",
            description=(
                "Обычная версия Minecraft от Mojang. Не требует модов и "
                "работает на любом хромбуке."
            ),
            website="https://www.minecraft.net",
            color="#5fa54b",
        )

    def list_versions(
        self,
        paths: Paths,
        http: Http,
        game_version: str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> List[LoaderVersion]:
        manifest = VersionManifest(paths, http)
        info = manifest.find(game_version, force=False)
        if info is None:
            return []
        return [
            LoaderVersion(
                version="",
                game_version=game_version,
                stable=info.type == "release",
                note=info.type_title,
            )
        ]

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
        # Для ванили достаточно сохранить описание версии локально.
        repo = VersionRepository(paths, http)
        raw = repo.load_raw(game_version, reporter=reporter)
        version_id = str(raw.get("id") or game_version)
        target = paths.version_json(version_id)
        if not target.is_file():
            import json

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        return version_id

    def matches(self, version_json: Mapping[str, object]) -> bool:
        return True
