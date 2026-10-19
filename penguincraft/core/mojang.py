"""Метаданные Minecraft: манифест версий, описания версий, наследование.

Forge, NeoForge, Fabric и Quilt выпускают *дочерние* описания версий,
которые ссылаются на ванильную через ``inheritsFrom``.  Лаунчер обязан
склеивать их ровно так же, как это делает официальный.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .net import Http
from .paths import Paths
from .task import Reporter

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
#: Сколько секунд считать манифест «свежим».
MANIFEST_TTL = 30 * 60


@dataclass
class VersionInfo:
    """Краткая запись о версии из манифеста."""

    id: str
    type: str
    url: str
    time: str = ""
    release_time: str = ""
    sha1: str = ""
    compliance_level: int = 0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VersionInfo":
        return cls(
            id=str(raw.get("id", "")),
            type=str(raw.get("type", "release")),
            url=str(raw.get("url", "")),
            time=str(raw.get("time", "")),
            release_time=str(raw.get("releaseTime", "")),
            sha1=str(raw.get("sha1", "")),
            compliance_level=int(raw.get("complianceLevel", 0) or 0),
        )

    @property
    def type_title(self) -> str:
        return {
            "release": "Релиз",
            "snapshot": "Снапшот",
            "old_beta": "Бета",
            "old_alpha": "Альфа",
        }.get(self.type, self.type)

    @property
    def release_date(self) -> str:
        text = self.release_time or self.time
        return text[:10] if text else ""


class VersionManifest:
    """Манифест всех версий Minecraft с локальным кешем."""

    def __init__(self, paths: Paths, http: Optional[Http] = None) -> None:
        self.paths = paths
        self.http = http or Http()
        self._data: Optional[Dict[str, Any]] = None

    @property
    def cache_path(self) -> Path:
        return self.paths.meta / "version_manifest_v2.json"

    # ------------------------------------------------------------------
    def _read_cache(self) -> Optional[Dict[str, Any]]:
        if not self.cache_path.is_file():
            return None
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, data: Mapping[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["_cached_at"] = time.time()
        tmp = self.cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.cache_path)

    def refresh(self) -> Dict[str, Any]:
        """Принудительно скачивает свежий манифест."""
        data = self.http.get_json(VERSION_MANIFEST_URL)
        if not isinstance(data, dict) or "versions" not in data:
            raise ValueError("Mojang вернул некорректный манифест версий")
        self._write_cache(data)
        self._data = data
        return data

    def load(self, *, force: bool = False, reporter: Optional[Reporter] = None) -> Dict[str, Any]:
        """Возвращает манифест, используя кеш, если он свежий."""
        if self._data is not None and not force:
            return self._data
        cached = self._read_cache()
        fresh = cached is not None and (
            time.time() - float(cached.get("_cached_at", 0)) < MANIFEST_TTL
        )
        if cached and fresh and not force:
            self._data = cached
            return cached
        try:
            if reporter:
                reporter.status("Обновление списка версий…")
            return self.refresh()
        except Exception:  # noqa: BLE001 - офлайн-режим
            if cached:
                self._data = cached
                return cached
            raise

    # ------------------------------------------------------------------
    def versions(
        self,
        *,
        include_snapshots: bool = False,
        include_old: bool = False,
        force: bool = False,
    ) -> List[VersionInfo]:
        data = self.load(force=force)
        allowed = {"release"}
        if include_snapshots:
            allowed.add("snapshot")
        if include_old:
            allowed.update({"old_beta", "old_alpha"})
        result = []
        for raw in data.get("versions", []):
            info = VersionInfo.from_dict(raw)
            if info.type in allowed:
                result.append(info)
        return result

    def latest(self, channel: str = "release") -> Optional[VersionInfo]:
        data = self.load()
        latest = data.get("latest") or {}
        version_id = latest.get(channel)
        if not version_id:
            for info in self.versions(include_snapshots=True, include_old=True):
                if info.type == channel:
                    return info
            return None
        return self.find(version_id)

    def find(self, version_id: str, *, force: bool = False) -> Optional[VersionInfo]:
        data = self.load(force=force)
        for raw in data.get("versions", []):
            if raw.get("id") == version_id:
                return VersionInfo.from_dict(raw)
        return None

    def all_ids(self) -> List[str]:
        data = self.load()
        return [str(raw.get("id", "")) for raw in data.get("versions", [])]


# ---------------------------------------------------------------------------
# Загрузка описаний версий
# ---------------------------------------------------------------------------


class VersionRepository:
    """Достаёт и склеивает описания версий."""

    def __init__(self, paths: Paths, http: Optional[Http] = None) -> None:
        self.paths = paths
        self.http = http or Http()
        self.manifest = VersionManifest(paths, self.http)
        self._memo: Dict[str, Dict[str, Any]] = {}

    # -- источники ----------------------------------------------------------
    def local_path(self, version_id: str) -> Path:
        return self.paths.version_json(version_id)

    def has_local(self, version_id: str) -> bool:
        return self.local_path(version_id).is_file()

    def list_local(self) -> List[str]:
        result: List[str] = []
        if not self.paths.versions.is_dir():
            return result
        for entry in sorted(self.paths.versions.iterdir()):
            if entry.is_dir() and (entry / f"{entry.name}.json").is_file():
                result.append(entry.name)
        return result

    def _meta_cache_path(self, version_id: str) -> Path:
        return self.paths.meta / "versions" / f"{version_id}.json"

    def load_raw(
        self,
        version_id: str,
        *,
        reporter: Optional[Reporter] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Возвращает «сырое» описание версии (без склейки наследства)."""
        local = self.local_path(version_id)
        if local.is_file() and not force:
            try:
                return json.loads(local.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        cache = self._meta_cache_path(version_id)
        if cache.is_file() and not force:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        info = self.manifest.find(version_id)
        if info is None or not info.url:
            raise FileNotFoundError(f"Версия {version_id} не найдена в манифесте Mojang")
        if reporter:
            reporter.status(f"Загрузка описания версии {version_id}…")
        data = self.http.get_json(info.url)
        if not isinstance(data, dict):
            raise ValueError(f"Некорректное описание версии {version_id}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data

    # -- склейка ------------------------------------------------------------
    def resolve(
        self,
        version_id: str,
        *,
        reporter: Optional[Reporter] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Возвращает полное описание версии с учётом ``inheritsFrom``."""
        if not force and version_id in self._memo:
            return self._memo[version_id]

        chain: List[Dict[str, Any]] = []
        seen: set[str] = set()
        current = version_id
        while current and current not in seen:
            seen.add(current)
            raw = self.load_raw(current, reporter=reporter, force=force)
            chain.append(raw)
            current = raw.get("inheritsFrom") or ""

        # chain[0] — сама версия, дальше — родители. Склеиваем от корня.
        merged: Dict[str, Any] = {}
        for raw in reversed(chain):
            merged = merge_version(merged, raw)
        merged.setdefault("id", version_id)
        self._memo[version_id] = merged
        return merged

    def clear(self, version_id: Optional[str] = None) -> None:
        if version_id:
            self._memo.pop(version_id, None)
        else:
            self._memo.clear()


# ---------------------------------------------------------------------------
# Склейка описаний
# ---------------------------------------------------------------------------

#: Поля, которые берутся у дочерней версии, если заданы.
_OVERRIDE_FIELDS = (
    "mainClass",
    "type",
    "logging",
    "javaVersion",
    "downloads",
    "assetIndex",
    "assets",
    "complianceLevel",
    "minimumLauncherVersion",
    "releaseTime",
    "time",
)


def merge_version(parent: Mapping[str, Any], child: Mapping[str, Any]) -> Dict[str, Any]:
    """Склеивает описания: *parent* — базовая версия, *child* — надстройка."""
    result: Dict[str, Any] = dict(parent)

    for key in _OVERRIDE_FIELDS:
        if key in child and child[key] is not None:
            result[key] = child[key]

    # libraries: сначала родительские, затем дочерние (важен порядок на CP).
    parent_libs = list(parent.get("libraries") or [])
    child_libs = list(child.get("libraries") or [])
    result["libraries"] = parent_libs + child_libs

    # arguments: тоже родитель → потомок.
    parent_args = parent.get("arguments") or {}
    child_args = child.get("arguments") or {}
    if parent_args or child_args:
        result["arguments"] = {
            "game": list(parent_args.get("game") or []) + list(child_args.get("game") or []),
            "jvm": list(parent_args.get("jvm") or []) + list(child_args.get("jvm") or []),
        }

    # Устаревший формат аргументов.
    if child.get("minecraftArguments"):
        result["minecraftArguments"] = str(child["minecraftArguments"])
    elif "minecraftArguments" in parent and "minecraftArguments" not in child:
        result["minecraftArguments"] = parent["minecraftArguments"]

    if child.get("id"):
        result["id"] = child["id"]
    result.pop("inheritsFrom", None)
    return result


def java_major_for(version: Mapping[str, Any]) -> int:
    """Требуемая мажорная версия Java по описанию версии."""
    java_version = version.get("javaVersion") or {}
    major = java_version.get("majorVersion")
    if major:
        return int(major)
    # Запасной вариант: по дате релиза / версии игры.
    version_id = str(version.get("id", ""))
    return guess_java_major(version_id)


#: Известные соответствия «версия игры → Java» для случаев, когда
#: в манифесте поля ``javaVersion`` нет.
def guess_java_major(version_id: str) -> int:
    release = _release_number(version_id)
    if release is None:
        return 8
    if release >= 20.5 or (release >= 21.0 and release < 100):
        return 21
    if release >= 18.0:
        return 17
    if release >= 17.0:
        return 16
    return 8


def _release_number(version_id: str) -> Optional[float]:
    """``1.20.5`` -> 20.5, ``1.8.9`` -> 8.9, ``26.1`` -> 26.1."""
    text = version_id
    for suffix in ("-pre", "-rc", "-pre1", " Pre-Release"):
        if suffix in text:
            text = text.split(suffix)[0]
    parts = text.split(".")
    if len(parts) < 2:
        return None
    try:
        if parts[0] == "1":
            major = int(parts[1])
            minor = int("".join(ch for ch in parts[2] if ch.isdigit()) or 0) if len(parts) > 2 else 0
            # 1.20.5 -> 20.5 ; 1.20 -> 20.0
            if len(parts) > 2:
                return float(f"{major}.{''.join(ch for ch in parts[2] if ch.isdigit()) or 0}")
            return float(major)
        # Новый формат нумерации (2026+: 26.1 и т.п.)
        return float(f"{int(parts[0])}.{''.join(ch for ch in parts[1] if ch.isdigit()) or 0}")
    except (ValueError, IndexError):
        return None
