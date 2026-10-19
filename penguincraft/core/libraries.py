"""Библиотеки: скачивание, classpath и распаковка natives."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .net import Http
from .paths import Paths
from .rules import default_platform, rules_allow
from .task import ProgressAggregator, Reporter
from .util import MavenCoordinate, extract_zip

#: Каталоги, которые не нужно распаковывать из natives-архивов.
DEFAULT_EXCLUDES = ("META-INF/",)

#: Официальный репозиторий, если у библиотеки не указан ``url``.
MOJANG_LIBRARIES_URL = "https://libraries.minecraft.net/"


@dataclass
class LibraryFile:
    """Файл библиотеки, который нужно положить в ``libraries``."""

    url: str
    relative: str
    sha1: str = ""
    size: int = 0

    @property
    def is_remote(self) -> bool:
        return bool(self.url)


@dataclass
class NativeFile:
    """Natives-архив, подлежащий распаковке."""

    url: str
    relative: str
    sha1: str = ""
    size: int = 0
    excludes: Tuple[str, ...] = DEFAULT_EXCLUDES
    classifier: str = ""


@dataclass
class LibraryPlan:
    """Результат разбора списка библиотек."""

    artifacts: List[LibraryFile] = field(default_factory=list)
    natives: List[NativeFile] = field(default_factory=list)

    def all_files(self) -> List[LibraryFile]:
        files = list(self.artifacts)
        for native in self.natives:
            files.append(LibraryFile(native.url, native.relative, native.sha1, native.size))
        return files


# ---------------------------------------------------------------------------
# Разбор описаний библиотек
# ---------------------------------------------------------------------------


def _artifact_from_downloads(downloads: Mapping[str, Any]) -> Optional[LibraryFile]:
    entry = downloads.get("artifact")
    if not isinstance(entry, Mapping):
        return None
    url = str(entry.get("url") or "")
    relative = str(entry.get("path") or "")
    if not relative:
        return None
    if not url:
        # Бывает у Fabric-подобных манифестов без downloads — путь строится
        # из имени, но здесь его нет, поэтому пропускаем.
        return None
    return LibraryFile(
        url=url,
        relative=relative,
        sha1=str(entry.get("sha1") or ""),
        size=int(entry.get("size") or 0),
    )


def _maven_fallback(library: Mapping[str, Any], extension: str = "jar") -> Optional[LibraryFile]:
    """Строит URL по имени, если ``downloads`` отсутствует."""
    name = library.get("name")
    if not name:
        return None
    try:
        coordinate = MavenCoordinate.parse(str(name))
    except ValueError:
        return None
    if extension != "jar":
        coordinate = MavenCoordinate(
            coordinate.group,
            coordinate.artifact,
            coordinate.version,
            coordinate.classifier,
            extension,
            coordinate.raw,
        )
    base = str(library.get("url") or MOJANG_LIBRARIES_URL)
    if not base.endswith("/"):
        base += "/"
    return LibraryFile(url=base + coordinate.path, relative=coordinate.path)


def _native_classifier(library: Mapping[str, Any], os_name: str) -> Optional[str]:
    natives = library.get("natives")
    if not isinstance(natives, Mapping):
        return None
    raw = natives.get(os_name)
    if not raw:
        return None
    return str(raw).replace("${arch}", "64" if _is_64bit() else "32")


def _is_64bit() -> bool:
    import sys

    return sys.maxsize > 2 ** 32


def build_plan(
    libraries: Sequence[Mapping[str, Any]],
    platform: Optional[Mapping[str, Any]] = None,
) -> LibraryPlan:
    """Определяет, что скачать и что распаковать."""
    plat = platform if platform is not None else default_platform()
    os_name = str(plat.get("name", "linux"))
    plan = LibraryPlan()

    for library in libraries:
        if not isinstance(library, Mapping):
            continue
        if not rules_allow(library.get("rules"), plat):
            continue

        downloads = library.get("downloads") or {}
        artifact = None
        if isinstance(downloads, Mapping):
            artifact = _artifact_from_downloads(downloads)
        if artifact is None:
            artifact = _maven_fallback(library)
        if artifact is not None:
            plan.artifacts.append(artifact)

        classifier = _native_classifier(library, os_name)
        if not classifier:
            continue

        native = None
        classifiers = downloads.get("classifiers") if isinstance(downloads, Mapping) else None
        if isinstance(classifiers, Mapping):
            entry = classifiers.get(classifier)
            if isinstance(entry, Mapping) and entry.get("path"):
                if entry.get("url"):
                    native = NativeFile(
                        url=str(entry["url"]),
                        relative=str(entry["path"]),
                        sha1=str(entry.get("sha1") or ""),
                        size=int(entry.get("size") or 0),
                        classifier=classifier,
                    )
        if native is None:
            # Старый формат: natives описаны просто именем с классификатором.
            name = str(library.get("name") or "")
            if not name:
                continue
            parts = name.split(":")
            if len(parts) < 3:
                continue
            coordinate = MavenCoordinate(
                parts[0], parts[1], parts[2], classifier, "jar", name
            )
            base = str(library.get("url") or MOJANG_LIBRARIES_URL)
            if not base.endswith("/"):
                base += "/"
            native = NativeFile(url=base + coordinate.path, relative=coordinate.path,
                                classifier=classifier)

        extract = library.get("extract") or {}
        excludes = tuple(extract.get("exclude") or DEFAULT_EXCLUDES) if isinstance(extract, Mapping) else DEFAULT_EXCLUDES
        native.excludes = excludes or DEFAULT_EXCLUDES
        plan.natives.append(native)

    return plan


# ---------------------------------------------------------------------------
# Установка
# ---------------------------------------------------------------------------


def install_libraries(
    paths: Paths,
    libraries: Sequence[Mapping[str, Any]],
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    version_id: str = "",
    platform: Optional[Mapping[str, Any]] = None,
    parallel: int = 8,
    clean_natives: bool = True,
) -> Tuple[List[Path], Path]:
    """Скачивает библиотеки и распаковывает natives.

    Возвращает ``(список путей classpath, каталог natives)``.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    plan = build_plan(libraries, platform)
    files = plan.all_files()
    aggregator = ProgressAggregator(reporter) if reporter else None
    if aggregator:
        aggregator.reset(len(files), "Проверка библиотек")

    missing: List[LibraryFile] = []
    resolved: Dict[str, Path] = {}
    for item in files:
        target = paths.libraries / item.relative
        resolved[item.relative] = target
        if target.is_file() and (not item.size or target.stat().st_size == item.size):
            continue
        if target.is_file() and not item.size:
            continue
        missing.append(item)

    if missing and reporter:
        reporter.status(f"Загрузка библиотек ({len(missing)} файлов)…")

    # Параллельные загрузки не должны рисовать собственный прогресс:
    # общий прогресс считает aggregator, иначе полоса «дрожит».
    quiet = reporter.no_progress() if reporter else None

    def fetch(item: LibraryFile) -> None:
        http.download(
            item.url,
            resolved[item.relative],
            sha1=item.sha1 or None,
            size=item.size or None,
            reporter=quiet,
        )
        if aggregator:
            aggregator.update(item.relative, 1)

    if missing:
        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            futures = [pool.submit(fetch, item) for item in missing]
            for future in as_completed(futures):
                future.result()
    if aggregator:
        for item in files:
            if item not in missing:
                aggregator.update(item.relative, 1)

    # classpath — только «обычные» артефакты, без natives-архивов
    classpath = [
        resolved[item.relative]
        for item in plan.artifacts
        if item.relative in resolved
    ]

    natives_dir = paths.natives_dir(version_id or "shared")
    if plan.natives:
        if clean_natives and natives_dir.is_dir():
            from .util import remove_tree

            remove_tree(natives_dir)
        natives_dir.mkdir(parents=True, exist_ok=True)
        for native in plan.natives:
            archive = resolved.get(native.relative)
            if archive is None or not archive.is_file():
                continue
            extract_zip(archive, natives_dir, exclude=native.excludes)
    else:
        natives_dir.mkdir(parents=True, exist_ok=True)

    return classpath, natives_dir


def classpath_string(entries: Iterable[Path]) -> str:
    import os

    return os.pathsep.join(str(entry) for entry in entries)
