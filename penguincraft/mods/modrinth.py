"""Клиент Modrinth (поиск модов, модпаков, ресурспаков, шейдеров).

Документация: https://docs.modrinth.com/api/
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .. import APP_NAME, APP_VERSION
from ..core.net import Http
from ..core.task import Reporter

BASE_URL = "https://api.modrinth.com/v2"

#: Загрузчики Modrinth, совместимые с нашими.
KNOWN_LOADERS = ("fabric", "quilt", "forge", "neoforge", "liteloader", "modloader", "risugami")


class ModrinthError(Exception):
    """Ошибка обращения к Modrinth."""


@dataclass
class SearchHit:
    """Результат поиска."""

    project_id: str
    slug: str
    title: str
    description: str
    project_type: str
    author: str = ""
    downloads: int = 0
    follows: int = 0
    icon_url: str = ""
    categories: List[str] = field(default_factory=list)
    versions: List[str] = field(default_factory=list)
    loaders: List[str] = field(default_factory=list)
    latest_version: str = ""
    license: str = ""
    updated: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SearchHit":
        categories = [str(item) for item in (raw.get("display_categories") or raw.get("categories") or [])]
        loaders = [item for item in categories if item in KNOWN_LOADERS]
        return cls(
            project_id=str(raw.get("project_id") or raw.get("id") or ""),
            slug=str(raw.get("slug") or ""),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            project_type=str(raw.get("project_type") or "mod"),
            author=str(raw.get("author") or ""),
            downloads=int(raw.get("downloads") or 0),
            follows=int(raw.get("follows") or 0),
            icon_url=str(raw.get("icon_url") or ""),
            categories=categories,
            versions=[str(item) for item in (raw.get("versions") or [])],
            loaders=loaders,
            latest_version=str(raw.get("latest_version") or ""),
            license=str((raw.get("license") or {}).get("id") or "") if isinstance(raw.get("license"), Mapping) else "",
            updated=str(raw.get("date_modified") or raw.get("updated") or ""),
        )


@dataclass
class VersionFile:
    """Файл внутри версии проекта."""

    url: str
    filename: str
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    primary: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VersionFile":
        hashes = raw.get("hashes") or {}
        return cls(
            url=str(raw.get("url") or ""),
            filename=str(raw.get("filename") or ""),
            sha1=str(hashes.get("sha1") or ""),
            sha512=str(hashes.get("sha512") or ""),
            size=int(raw.get("size") or 0),
            primary=bool(raw.get("primary", False)),
        )


@dataclass
class ModVersion:
    """Версия проекта (мода/модпака)."""

    id: str
    project_id: str
    name: str
    version_number: str
    version_type: str            # release | beta | alpha
    loaders: List[str] = field(default_factory=list)
    game_versions: List[str] = field(default_factory=list)
    downloads: int = 0
    date_published: str = ""
    files: List[VersionFile] = field(default_factory=list)
    dependencies: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModVersion":
        return cls(
            id=str(raw.get("id") or ""),
            project_id=str(raw.get("project_id") or ""),
            name=str(raw.get("name") or ""),
            version_number=str(raw.get("version_number") or ""),
            version_type=str(raw.get("version_type") or "release"),
            loaders=[str(item) for item in (raw.get("loaders") or [])],
            game_versions=[str(item) for item in (raw.get("game_versions") or [])],
            downloads=int(raw.get("downloads") or 0),
            date_published=str(raw.get("date_published") or ""),
            files=[VersionFile.from_dict(item) for item in (raw.get("files") or [])],
            dependencies=list(raw.get("dependencies") or []),
        )

    @property
    def primary_file(self) -> Optional[VersionFile]:
        for file in self.files:
            if file.primary:
                return file
        return self.files[0] if self.files else None

    @property
    def type_title(self) -> str:
        return {"release": "релиз", "beta": "бета", "alpha": "альфа"}.get(self.version_type, self.version_type)


@dataclass
class SearchResult:
    hits: List[SearchHit]
    total: int
    offset: int
    limit: int


class Modrinth:
    """Тонкая обёртка над REST API Modrinth."""

    def __init__(self, http: Optional[Http] = None) -> None:
        self.http = http or Http(
            user_agent=f"{APP_NAME}/{APP_VERSION} (linux chromeos launcher)"
        )
        self._cache: Dict[str, Tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    def _get(self, path: str, *, params: Optional[Dict[str, str]] = None, ttl: float = 120.0) -> Any:
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        cached = self._cache.get(url)
        if cached and (time.time() - cached[0]) < ttl:
            return cached[1]
        try:
            data = self.http.get_json(url)
        except Exception as error:  # noqa: BLE001
            if cached:
                return cached[1]
            raise ModrinthError(f"Modrinth недоступен: {error}") from error
        self._cache[url] = (time.time(), data)
        return data

    # ------------------------------------------------------------------
    def search(
        self,
        query: str = "",
        *,
        project_type: str = "",
        loaders: Sequence[str] = (),
        game_versions: Sequence[str] = (),
        categories: Sequence[str] = (),
        index: str = "relevance",
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResult:
        facets: List[List[str]] = []
        if project_type:
            facets.append([f"project_type:{project_type}"])
        if loaders:
            facets.append([f"categories:{item}" for item in loaders])
        if game_versions:
            facets.append([f"versions:{item}" for item in game_versions])
        if categories:
            facets.append([f"categories:{item}" for item in categories])

        params: Dict[str, str] = {
            "limit": str(max(1, min(limit, 100))),
            "offset": str(max(0, offset)),
            "index": index,
        }
        if query.strip():
            params["query"] = query.strip()
        if facets:
            params["facets"] = json.dumps(facets, separators=(",", ":"))

        data = self._get("/search", params=params, ttl=60.0)
        if not isinstance(data, Mapping):
            return SearchResult([], 0, offset, limit)
        hits = [SearchHit.from_dict(item) for item in data.get("hits", [])]
        return SearchResult(
            hits=hits,
            total=int(data.get("total_hits") or len(hits)),
            offset=int(data.get("offset") or offset),
            limit=int(data.get("limit") or limit),
        )

    # ------------------------------------------------------------------
    def project(self, project_id: str) -> Dict[str, Any]:
        data = self._get(f"/project/{urllib.parse.quote(project_id)}", ttl=600.0)
        return data if isinstance(data, dict) else {}

    def projects(self, project_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Пакетно получает проекты (порциями по 100) → ``id → данные``."""
        unique = [pid for pid in dict.fromkeys(project_ids) if pid]
        result: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(unique), 100):
            chunk = unique[start : start + 100]
            params = {"ids": json.dumps(chunk, separators=(",", ":"))}
            try:
                data = self._get("/projects", params=params, ttl=600.0)
            except ModrinthError:
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                if isinstance(item, Mapping):
                    key = str(item.get("id") or "")
                    if key:
                        result[key] = dict(item)
        return result

    def icon_urls(self, project_ids: Sequence[str]) -> Dict[str, str]:
        """Возвращает ``project_id → URL иконки``.

        Modrinth в поиске отдаёт иконки в формате WebP, который Tk не умеет
        читать, поэтому берём «сырой» оригинал (обычно PNG).
        """
        mapping: Dict[str, str] = {}
        for pid, project in self.projects(project_ids).items():
            url = project.get("raw_icon_url") or project.get("icon_url") or ""
            if url:
                mapping[pid] = str(url)
        return mapping

    def versions(
        self,
        project_id: str,
        *,
        loaders: Sequence[str] = (),
        game_versions: Sequence[str] = (),
    ) -> List[ModVersion]:
        params: Dict[str, str] = {}
        if loaders:
            params["loaders"] = json.dumps(list(loaders), separators=(",", ":"))
        if game_versions:
            params["game_versions"] = json.dumps(list(game_versions), separators=(",", ":"))
        data = self._get(
            f"/project/{urllib.parse.quote(project_id)}/version",
            params=params or None,
            ttl=120.0,
        )
        if not isinstance(data, list):
            return []
        return [ModVersion.from_dict(item) for item in data]

    def version(self, version_id: str) -> Optional[ModVersion]:
        try:
            data = self._get(f"/version/{urllib.parse.quote(version_id)}", ttl=600.0)
        except ModrinthError:
            return None
        return ModVersion.from_dict(data) if isinstance(data, Mapping) else None

    # ------------------------------------------------------------------
    def best_version(
        self,
        project_id: str,
        *,
        loader: str = "",
        game_version: str = "",
        prefer_release: bool = True,
    ) -> Optional[ModVersion]:
        """Подбирает наиболее подходящую версию проекта."""
        loaders = [loader] if loader and loader != "vanilla" else []
        versions = self.versions(project_id, loaders=loaders, game_versions=[game_version] if game_version else [])
        if not versions and (loaders or game_version):
            versions = self.versions(project_id)
        if not versions:
            return None

        def score(version: ModVersion) -> Tuple[int, int, str]:
            type_score = {"release": 2, "beta": 1, "alpha": 0}.get(version.version_type, 0)
            if not prefer_release:
                type_score = 2
            game_score = 1 if (game_version and game_version in version.game_versions) else 0
            loader_score = 1 if (loader and loader in version.loaders) else 0
            return (type_score + game_score * 2 + loader_score * 2, version.downloads, version.date_published)

        candidates = [item for item in versions if item.files]
        if not candidates:
            return None
        return max(candidates, key=score)

    # ------------------------------------------------------------------
    def download_file(
        self,
        file: VersionFile,
        destination: Path | str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> Path:
        target = Path(destination)
        if target.is_dir():
            target = target / file.filename
        algo = "sha512" if file.sha512 else ("sha1" if file.sha1 else None)
        expected = file.sha512 or file.sha1 or None
        if algo and expected:
            try:
                from ..core.util import hash_file

                if target.is_file() and hash_file(target, algo) == expected:
                    return target
            except OSError:
                pass
        if self.http is not None and file.url:
            self.http.download(file.url, target, reporter=reporter)
            return target
        raise ModrinthError(f"Нельзя скачать файл {file.filename}: нет ссылки")

    def tags(self, kind: str) -> List[Dict[str, Any]]:
        try:
            data = self._get(f"/tag/{kind}", ttl=6 * 3600)
        except ModrinthError:
            return []
        return list(data) if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Вспомогательные преобразования
# ---------------------------------------------------------------------------


def format_downloads(count: int) -> str:
    """1 234 567 -> «1,2 млн»."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f} млн".replace(".", ",")
    if count >= 1_000:
        return f"{count / 1_000:.0f} тыс.".replace(".", ",")
    return str(count)
