"""Игровые ресурсы (assets): индексы, объекты, legacy/virtual-каталоги."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .net import Http
from .paths import Paths
from .task import ProgressAggregator, Reporter

RESOURCES_URL = "https://resources.download.minecraft.net"


@dataclass
class AssetResult:
    """Итог установки ресурсов."""

    index_id: str = ""
    assets_root: Optional[Path] = None
    virtual_dir: Optional[Path] = None
    legacy_assets_root: Optional[Path] = None
    downloaded: int = 0
    already_present: int = 0
    total_objects: int = 0

    @property
    def game_assets(self) -> str:
        return str(self.virtual_dir) if self.virtual_dir else ""


def _object_target(paths: Paths, object_hash: str) -> Path:
    return paths.asset_objects / object_hash[:2] / object_hash


def load_index(paths: Paths, index_id: str) -> Optional[Dict[str, Any]]:
    """Читает уже скачанный индекс ресурсов."""
    index_file = paths.asset_indexes / f"{index_id}.json"
    if not index_file.is_file():
        return None
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure_index(
    paths: Paths,
    version: Mapping[str, Any],
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Скачивает JSON-индекс ресурсов для версии."""
    asset_index = version.get("assetIndex") or {}
    index_id = str(asset_index.get("id") or version.get("assets") or "")
    if not index_id:
        return None

    existing = None if force else load_index(paths, index_id)
    if existing is not None:
        return existing

    url = str(asset_index.get("url") or "")
    if not url:
        # Можно собрать URL по известному шаблону.
        url = f"https://piston-meta.mojang.com/v1/packages/{index_id}/{index_id}.json"
    if reporter:
        reporter.status(f"Загрузка индекса ресурсов {index_id}…")
    target = paths.asset_indexes / f"{index_id}.json"
    http.download(
        url,
        target,
        sha1=str(asset_index.get("sha1") or "") or None,
        size=int(asset_index.get("size") or 0) or None,
        reporter=reporter,
        force=force,
    )
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Некорректный индекс ресурсов {index_id}: {error}") from error


def install_assets(
    paths: Paths,
    version: Mapping[str, Any],
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    game_dir: Optional[Path] = None,
    parallel: int = 8,
    force: bool = False,
) -> AssetResult:
    """Скачивает все ресурсы, нужные версии."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    index = ensure_index(paths, version, http, reporter=reporter, force=force)
    result = AssetResult(
        index_id=str((version.get("assetIndex") or {}).get("id") or version.get("assets") or ""),
        assets_root=paths.assets,
    )
    if index is None:
        return result

    objects: Mapping[str, Any] = index.get("objects") or {}
    result.total_objects = len(objects)
    if not objects:
        return result

    pending: List[Tuple[str, Path]] = []
    for _, meta in objects.items():
        if not isinstance(meta, Mapping):
            continue
        object_hash = str(meta.get("hash") or "")
        if not object_hash:
            continue
        target = _object_target(paths, object_hash)
        expected_size = int(meta.get("size") or 0)
        if target.is_file() and (not expected_size or target.stat().st_size == expected_size):
            result.already_present += 1
            continue
        pending.append((f"{RESOURCES_URL}/{object_hash[:2]}/{object_hash}", target))

    result.downloaded = len(pending)
    if pending and reporter:
        reporter.status(
            f"Загрузка ресурсов: {len(pending)} новых из {result.total_objects}…"
        )

    if pending:
        aggregator = ProgressAggregator(reporter) if reporter else None
        if aggregator:
            aggregator.reset(len(pending), "Загрузка ресурсов")

        quiet = reporter.no_progress() if reporter else None

        def fetch(index_number: int, url: str, target: Path) -> None:
            # Хеш файла совпадает с его именем — используем как контроль.
            expected = target.name
            http.download(url, target, sha1=expected, reporter=quiet)
            if aggregator:
                aggregator.update(str(index_number), 1)

        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            futures = [
                pool.submit(fetch, number, url, target)
                for number, (url, target) in enumerate(pending)
            ]
            for future in as_completed(futures):
                future.result()

    # Старые версии (до 1.7.3) читают ресурсы как обычные файлы.
    if index.get("virtual"):
        result.virtual_dir = _materialize(
            paths, objects, paths.assets / "virtual" / result.index_id, use_links=True
        )
    if index.get("map_to_resources") and game_dir is not None:
        result.legacy_assets_root = _materialize(
            paths, objects, Path(game_dir) / "resources", use_links=True
        )
    return result


def _materialize(
    paths: Paths,
    objects: Mapping[str, Any],
    destination: Path,
    *,
    use_links: bool = True,
) -> Path:
    """Раскладывает объекты по «человеческим» именам (legacy/virtual)."""
    destination.mkdir(parents=True, exist_ok=True)
    for name, meta in objects.items():
        if not isinstance(meta, Mapping):
            continue
        object_hash = str(meta.get("hash") or "")
        if not object_hash:
            continue
        source = _object_target(paths, object_hash)
        if not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        linked = False
        if use_links:
            try:
                relative = Path(
                    # относительная ссылка, чтобы каталог можно было переносить
                    __import__("os").path.relpath(source, target.parent)
                )
                target.symlink_to(relative)
                linked = True
            except (OSError, NotImplementedError, ValueError):
                linked = False
        if not linked:
            try:
                shutil.copy2(source, target)
            except OSError:
                continue
    return destination


def assets_complete(paths: Paths, version: Mapping[str, Any]) -> bool:
    """Грубая проверка: все ли ресурсы на месте."""
    index_id = str((version.get("assetIndex") or {}).get("id") or "")
    if not index_id:
        return True
    index = load_index(paths, index_id)
    if index is None:
        return False
    objects = index.get("objects") or {}
    checked = 0
    for _, meta in objects.items():
        if checked >= 400:  # не сканируем полторы тысячи файлов каждый раз
            break
        if not isinstance(meta, Mapping):
            continue
        object_hash = str(meta.get("hash") or "")
        if object_hash:
            checked += 1
            if not _object_target(paths, object_hash).is_file():
                return False
    return True
