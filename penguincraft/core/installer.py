"""Установка версии: клиент игры, библиотеки, ресурсы и Java."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .assets import AssetResult, install_assets
from .java import JavaRuntime, ensure_java
from .libraries import install_libraries
from .mojang import VersionRepository, java_major_for
from .net import Http
from .paths import Paths
from .rules import default_platform
from .settings import Settings
from .task import Reporter
from .util import hash_file


class InstallError(Exception):
    """Не удалось установить версию."""


@dataclass
class InstallPlan:
    """Всё, что нужно для запуска версии."""

    version_id: str
    version: Dict[str, Any]
    game_dir: Path
    classpath: List[Path] = field(default_factory=list)
    natives_dir: Optional[Path] = None
    client_jar: Optional[Path] = None
    assets: Optional[AssetResult] = None
    java: Optional[JavaRuntime] = None
    logging_config: Optional[Path] = None

    @property
    def classpath_string(self) -> str:
        return os.pathsep.join(str(entry) for entry in self.classpath)


# ---------------------------------------------------------------------------
# Клиентский jar
# ---------------------------------------------------------------------------


def client_jar_path(paths: Paths, version_id: str, version: Mapping[str, Any]) -> Path:
    """Куда положить клиент игры (учитываем поле ``jar`` у старых версий)."""
    jar_id = str(version.get("jar") or version_id)
    return paths.versions / jar_id / f"{jar_id}.jar"


def _try_hardlink(source: Path, target: Path) -> bool:
    """Экономит место на диске хромбука: одинаковые jar'ы — ссылкой."""
    try:
        if target.exists():
            target.unlink()
        os.link(source, target)
        return True
    except OSError:
        return False


def ensure_client_jar(
    paths: Paths,
    version_id: str,
    version: Mapping[str, Any],
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
) -> Path:
    """Скачивает клиент игры, если его нет."""
    downloads = version.get("downloads") or {}
    client = downloads.get("client") if isinstance(downloads, Mapping) else None
    target = client_jar_path(paths, version_id, version)
    target.parent.mkdir(parents=True, exist_ok=True)

    if not client:
        # Унаследованная версия без downloads: ищем подходящий jar рядом.
        if target.is_file():
            return target
        for candidate in paths.versions.glob("*/*.jar"):
            if candidate.name == f"{version.get('jar') or version_id}.jar":
                if _try_hardlink(candidate, target):
                    return target
        raise InstallError(
            f"Для версии {version_id} не указан клиентский jar и его не удалось найти"
        )

    url = str(client.get("url") or "")
    sha1 = str(client.get("sha1") or "")
    size = int(client.get("size") or 0)

    if target.is_file() and (not size or target.stat().st_size == size):
        return target

    # Пробуем обойтись ссылкой на уже скачанный файл с тем же хешем.
    if sha1:
        for candidate in paths.versions.glob("*/*.jar"):
            try:
                if candidate.stat().st_size == size and candidate.is_file():
                    if hash_file(candidate, "sha1") == sha1:
                        if _try_hardlink(candidate, target):
                            if reporter:
                                reporter.log(
                                    f"Клиент {version_id} связан ссылкой с {candidate.parent.name}"
                                )
                            return target
                        shutil.copy2(candidate, target)
                        return target
            except OSError:
                continue

    if reporter:
        reporter.status(f"Загрузка клиента {version_id}…")
    http.download(url, target, sha1=sha1 or None, size=size or None, reporter=reporter)
    return target


# ---------------------------------------------------------------------------
# Конфигурация логирования
# ---------------------------------------------------------------------------


def ensure_logging_config(
    paths: Paths,
    version: Mapping[str, Any],
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
) -> Optional[Path]:
    """Готовит конфиг log4j с читаемым выводом в консоль.

    Официальный конфиг Mojang пишет в консоль XML-события
    (``LegacyXMLLayout``) — их понимает только лаунчер Mojang.  Мы берём
    тот же файл и заменяем раскладку консоли на текстовый шаблон, оставляя
    запись ``logs/latest.log`` нетронутой.
    """
    logging = version.get("logging") or {}
    client = logging.get("client") if isinstance(logging, Mapping) else None
    if not isinstance(client, Mapping):
        return None
    file_info = client.get("file") or {}
    if not isinstance(file_info, Mapping) or not file_info.get("url"):
        return None
    config_id = str(file_info.get("id") or "client-log4j.xml")
    source = paths.meta / "log_configs" / config_id
    if not source.is_file() or (
        file_info.get("size") and source.stat().st_size != int(file_info["size"])
    ):
        try:
            http.download(
                str(file_info["url"]),
                source,
                sha1=str(file_info.get("sha1") or "") or None,
                size=int(file_info.get("size") or 0) or None,
                reporter=reporter,
            )
        except Exception:  # noqa: BLE001 - без конфига игра запустится
            return None

    friendly = source.with_name(source.stem + ".console.xml")
    try:
        patched = _make_console_friendly_config(source)
    except Exception:  # noqa: BLE001
        return source
    if patched is None:
        return source
    try:
        friendly.parent.mkdir(parents=True, exist_ok=True)
        friendly.write_text(patched, encoding="utf-8")
    except OSError:
        return source
    return friendly


#: Шаблон вывода в консоль лаунчера.
CONSOLE_PATTERN = "[%d{HH:mm:ss}] [%t/%level]: %msg{nolookups}%n"


def _make_console_friendly_config(source: Path) -> Optional[str]:
    """Заменяет XML-раскладки в конфиге log4j на текстовый PatternLayout."""
    import xml.etree.ElementTree as ElementTree

    tree = ElementTree.parse(source)
    root = tree.getroot()
    changed = False
    for console in root.iter("Console"):
        for child in list(console):
            tag = child.tag.split("}")[-1]
            if tag in ("LegacyXMLLayout", "XMLLayout", "PatternLayout"):
                console.remove(child)
        ElementTree.SubElement(
            console,
            "PatternLayout",
            {"pattern": CONSOLE_PATTERN},
        )
        changed = True
    if not changed:
        return None
    body = ElementTree.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


# ---------------------------------------------------------------------------
# Полная установка
# ---------------------------------------------------------------------------


def install_version(
    paths: Paths,
    settings: Settings,
    version_id: str,
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    game_dir: Optional[Path] = None,
    repository: Optional[VersionRepository] = None,
    java_path: str = "",
    allow_java_download: bool = True,
    download_assets: bool = True,
    force_version_json: bool = False,
    install_libraries_flag: bool = True,
) -> InstallPlan:
    """Готовит версию к запуску и возвращает план запуска."""
    paths.ensure_base_dirs()
    repo = repository or VersionRepository(paths, http)
    game_dir = Path(game_dir) if game_dir else paths.instance_game_dir("default")
    game_dir.mkdir(parents=True, exist_ok=True)

    top = reporter.child(start=0.0, span=1.0) if reporter else None

    def status(text: str) -> None:
        if reporter:
            reporter.status(text)

    # 1. Описание версии --------------------------------------------------
    if top:
        top.status(f"Чтение описания {version_id}…")
    try:
        version = repo.resolve(version_id, reporter=reporter, force=force_version_json)
    except FileNotFoundError as error:
        raise InstallError(str(error)) from error
    if top:
        top.check_cancel()

    platform = default_platform()

    # 2. Java --------------------------------------------------------------
    required_java = java_major_for(version)
    if top:
        top.status(f"Поиск Java {required_java}…")
    try:
        runtime = ensure_java(
            paths,
            required_java,
            http,
            reporter=reporter,
            preferred_path=java_path or settings.java_path,
            allow_download=allow_java_download and settings.auto_download_java,
        )
    except RuntimeError as error:
        raise InstallError(str(error)) from error
    if top:
        top.check_cancel()

    # 3. Клиент игры и логирование ----------------------------------------
    client_jar = ensure_client_jar(paths, version_id, version, http, reporter=reporter)
    logging_config = ensure_logging_config(paths, version, http, reporter=reporter)

    # 4. Библиотеки --------------------------------------------------------
    if install_libraries_flag:
        try:
            classpath, natives_dir = install_libraries(
                paths,
                version.get("libraries") or [],
                http,
                reporter=reporter,
                version_id=version_id,
                platform=platform,
                parallel=settings.parallel_downloads,
            )
        except Exception as error:  # noqa: BLE001
            raise InstallError(f"Ошибка загрузки библиотек: {error}") from error
    else:
        classpath, natives_dir = [], paths.natives_dir(version_id)

    # 5. Ресурсы -----------------------------------------------------------
    asset_result: Optional[AssetResult] = None
    if download_assets:
        try:
            asset_result = install_assets(
                paths,
                version,
                http,
                reporter=reporter,
                game_dir=game_dir,
                parallel=settings.parallel_downloads,
            )
        except Exception as error:  # noqa: BLE001
            raise InstallError(f"Ошибка загрузки ресурсов: {error}") from error

    # 6. Клиентский jar в classpath ---------------------------------------
    full_classpath = list(classpath)
    if client_jar not in full_classpath:
        full_classpath.append(client_jar)

    if top:
        top.done(text="Установка завершена")
    return InstallPlan(
        version_id=version_id,
        version=version,
        game_dir=game_dir,
        classpath=full_classpath,
        natives_dir=natives_dir,
        client_jar=client_jar,
        assets=asset_result,
        java=runtime,
        logging_config=logging_config,
    )
