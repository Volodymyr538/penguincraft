"""Установка модпаков в формате ``.mrpack`` (Modrinth Modpack).

Формат — обычный zip с файлом ``modrinth.index.json`` внутри,
каталогами ``overrides/`` (общие файлы) и ``client-overrides/``
(только для клиента).
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..core.instances import InstanceManager
from ..core.net import Http
from ..core.paths import Paths
from ..core.settings import InstanceConfig, Settings
from ..core.task import Cancelled, ProgressAggregator, Reporter
from ..loaders import get_loader

INDEX_NAME = "modrinth.index.json"

#: Соответствие ключей зависимостей Modrinth нашим загрузчикам.
DEPENDENCY_LOADERS = {
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}


class ModpackError(Exception):
    """Ошибка установки модпака."""


@dataclass
class ModpackFile:
    """Файл, который нужно скачать при установке модпака."""

    path: str
    downloads: List[str] = field(default_factory=list)
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    client: str = "required"     # required | optional | unsupported
    server: str = "required"

    @property
    def client_needed(self) -> bool:
        return self.client != "unsupported"

    @property
    def url(self) -> str:
        return self.downloads[0] if self.downloads else ""


@dataclass
class ModpackIndex:
    """Разобранный ``modrinth.index.json``."""

    name: str = ""
    version_id: str = ""
    summary: str = ""
    minecraft: str = ""
    loader: str = ""
    loader_version: str = ""
    files: List[ModpackFile] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)

    @property
    def client_files(self) -> List[ModpackFile]:
        return [item for item in self.files if item.client_needed]


def read_index(archive: Path | str) -> ModpackIndex:
    """Читает индекс модпака из zip-файла."""
    try:
        with zipfile.ZipFile(archive) as zf:
            if INDEX_NAME not in zf.namelist():
                raise ModpackError(
                    "Это не модпак Modrinth: внутри нет modrinth.index.json"
                )
            raw = json.loads(zf.read(INDEX_NAME).decode("utf-8"))
    except zipfile.BadZipFile as error:
        raise ModpackError("Файл модпака повреждён или это не zip-архив") from error
    return index_from_dict(raw)


def index_from_dict(raw: Mapping[str, Any]) -> ModpackIndex:
    dependencies = {str(k): str(v) for k, v in (raw.get("dependencies") or {}).items()}
    index = ModpackIndex(
        name=str(raw.get("name") or "Модпак"),
        version_id=str(raw.get("versionId") or ""),
        summary=str(raw.get("summary") or ""),
        dependencies=dependencies,
    )
    index.minecraft = dependencies.get("minecraft", "")
    for key, loader_id in DEPENDENCY_LOADERS.items():
        if key in dependencies:
            index.loader = loader_id
            index.loader_version = dependencies[key]
            break

    for item in raw.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        hashes = item.get("hashes") or {}
        env = item.get("env") or {}
        path = str(item.get("path") or "")
        if not path:
            continue
        index.files.append(
            ModpackFile(
                path=path,
                downloads=[str(url) for url in (item.get("downloads") or [])],
                sha1=str(hashes.get("sha1") or ""),
                sha512=str(hashes.get("sha512") or ""),
                size=int(item.get("fileSize") or 0),
                client=str(env.get("client") or "required"),
                server=str(env.get("server") or "required"),
            )
        )
    return index


# ---------------------------------------------------------------------------
# Установка
# ---------------------------------------------------------------------------


def install_modpack(
    paths: Paths,
    settings: Settings,
    instance_manager: InstanceManager,
    http: Http,
    archive: Path | str,
    *,
    display_name: str = "",
    reporter: Optional[Reporter] = None,
    icon: str = "📦",
    install_loader: bool = True,
) -> Tuple[InstanceConfig, ModpackIndex]:
    """Ставит модпак как отдельный экземпляр."""
    archive = Path(archive)
    index = read_index(archive)
    if not index.minecraft:
        raise ModpackError("В модпаке не указана версия Minecraft")

    name = display_name or index.name or archive.stem
    if reporter:
        reporter.status(f"Создание экземпляра «{name}»…")

    instance = instance_manager.create(
        name,
        version_id="",
        game_version=index.minecraft,
        loader=index.loader or "vanilla",
        loader_version=index.loader_version,
        icon=icon,
        notes=index.summary or f"Модпак {index.name} {index.version_id}".strip(),
    )

    try:
        # --- загрузчик + версия ------------------------------------------
        if install_loader:
            loader = get_loader(instance.loader)
            child = reporter.child(start=0.0, span=0.45) if reporter else None
            version_id = loader.install(
                paths,
                settings,
                http,
                index.minecraft,
                index.loader_version,
                reporter=child or reporter,
            )
            instance.version_id = version_id
            instance_manager.save(instance)

        # --- файлы модпака ------------------------------------------------
        child = reporter.child(start=0.45, span=0.5) if reporter else None
        _download_files(paths, http, instance_manager, instance, index, reporter=child)

        # --- overrides -----------------------------------------------------
        child = reporter.child(start=0.95, span=0.05) if reporter else None
        _apply_overrides(archive, instance_manager.game_dir(instance.name), reporter=child)
    except BaseException:
        # не оставляем мусор после неудачной установки
        try:
            instance_manager.delete(instance.name)
        except Exception:  # noqa: BLE001
            pass
        raise

    if reporter:
        reporter.log(
            f"Модпак «{index.name}» установлен: {len(index.client_files)} файлов"
        )
    return instance, index


def update_modpack(
    paths: Paths,
    settings: Settings,
    instance_manager: InstanceManager,
    http: Http,
    instance: InstanceConfig,
    archive: Path | str,
    *,
    old_files: Iterable[str] = (),
    reporter: Optional[Reporter] = None,
    install_loader: bool = True,
) -> ModpackIndex:
    """Обновляет существующий экземпляр новым ``.mrpack`` того же модпака.

    Файлы новой версии кладутся поверх старых, а те, что исчезли из
    модпака, удаляются — иначе в каталоге оставался бы «мусор».
    """
    archive = Path(archive)
    index = read_index(archive)
    if not index.minecraft:
        raise ModpackError("В модпаке не указана версия Minecraft")

    if install_loader:
        loader = get_loader(index.loader or instance.loader)
        child = reporter.child(start=0.0, span=0.45) if reporter else None
        version_id = loader.install(
            paths,
            settings,
            http,
            index.minecraft,
            index.loader_version,
            reporter=child or reporter,
        )
        instance.version_id = version_id
    if index.loader:
        instance.loader = index.loader
    if index.loader_version:
        instance.loader_version = index.loader_version
    instance.game_version = index.minecraft
    instance_manager.save(instance)

    child = reporter.child(start=0.45, span=0.5) if reporter else None
    _download_files(paths, http, instance_manager, instance, index, reporter=child)

    child = reporter.child(start=0.95, span=0.05) if reporter else None
    applied = _apply_overrides(archive, instance_manager.game_dir(instance.name), reporter=child)

    # Убираем файлы, которые были в прошлой версии, но исчезли в новой.
    # Файлы из overrides новой версии тоже считаются «новыми».
    new_paths = {item.path for item in index.client_files} | applied
    game_dir = instance_manager.game_dir(instance.name)
    removed = 0
    for relative in old_files:
        if not relative or relative in new_paths:
            continue
        target = game_dir / relative
        if target.is_file():
            try:
                target.unlink()
                removed += 1
            except OSError:
                continue
    if reporter and removed:
        reporter.log(f"Удалено устаревших файлов: {removed}")
    return index


def _download_files(
    paths: Paths,
    http: Http,
    manager: InstanceManager,
    instance: InstanceConfig,
    index: ModpackIndex,
    *,
    reporter: Optional[Reporter] = None,
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    game_dir = manager.game_dir(instance.name)
    files = index.client_files
    if not files:
        return

    aggregator = ProgressAggregator(reporter) if reporter else None
    if aggregator:
        aggregator.reset(len(files), "Загрузка файлов модпака")

    pending: List[ModpackFile] = []
    for item in files:
        target = game_dir / item.path
        if target.is_file() and (not item.size or target.stat().st_size == item.size):
            if aggregator:
                aggregator.update(item.path, 1)
            continue
        pending.append(item)

    if reporter:
        reporter.status(
            f"Загрузка модов модпака: {len(pending)} из {len(files)}…"
        )

    def fetch(item: ModpackFile) -> None:
        target = game_dir / item.path
        if not item.url:
            if reporter:
                reporter.log(f"Пропущен файл без ссылки: {item.path}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        http.download(
            item.url,
            target,
            sha1=item.sha1 or None,
            size=item.size or None,
            reporter=quiet,
        )
        if aggregator:
            aggregator.update(item.path, 1)

    quiet = reporter.no_progress() if reporter else None

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(pending)))) as pool:
            futures = [pool.submit(fetch, item) for item in pending]
            for future in as_completed(futures):
                try:
                    future.result()
                except Cancelled:
                    raise
                except Exception as error:  # noqa: BLE001
                    # отдельный мод может пропасть из CDN — не рушим установку
                    if reporter:
                        reporter.log(f"Не удалось скачать файл: {error}")


def _apply_overrides(
    archive: Path,
    game_dir: Path,
    *,
    reporter: Optional[Reporter] = None,
) -> set[str]:
    """Копирует overrides/ и client-overrides/ в каталог игры.

    Возвращает набор относительных путей, которые были записаны: он нужен,
    чтобы при обновлении модпака не удалить файлы, пришедшие из overrides.
    """
    applied: set[str] = set()
    prefix = "client-overrides/"
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            if name.startswith("overrides/"):
                relative = name[len("overrides/"):]
            elif name.startswith(prefix):
                relative = name[len(prefix):]
            elif name.startswith("server-overrides/"):
                continue
            else:
                continue
            if not relative:
                continue
            destination = game_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)
            applied.add(Path(relative).as_posix())
    if reporter:
        reporter.status("Файлы модпака распакованы")
    return applied


def detect_modpack_kind(archive: Path | str) -> str:
    """Определяет тип архива: ``mrpack``, ``curseforge`` или ``unknown``."""
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return "unknown"
    if INDEX_NAME in names:
        return "mrpack"
    if "manifest.json" in names and any(name.startswith("modlist.html") or "overrides/" in name for name in names):
        return "curseforge"
    if "manifest.json" in names:
        return "curseforge"
    return "unknown"


def extract_mrpack_meta(archive: Path | str) -> Dict[str, Any]:
    """Краткая информация о модпаке (для предпросмотра перед установкой)."""
    index = read_index(archive)
    return {
        "name": index.name,
        "version": index.version_id,
        "summary": index.summary,
        "minecraft": index.minecraft,
        "loader": index.loader,
        "loader_version": index.loader_version,
        "files": len(index.client_files),
        "size": sum(item.size for item in index.client_files),
    }
