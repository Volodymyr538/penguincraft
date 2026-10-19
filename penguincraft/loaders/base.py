"""Общие части для загрузчиков модов (Fabric, Quilt, Forge, NeoForge)."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional

from ..core.java import ensure_java
from ..core.mojang import VersionRepository, java_major_for
from ..core.net import Http
from ..core.paths import Paths
from ..core.settings import Settings
from ..core.task import Reporter
from ..core.util import write_launcher_profiles
from ..core.installer import ensure_client_jar


class LoaderError(Exception):
    """Не удалось установить загрузчик."""


@dataclass
class LoaderVersion:
    """Конкретная версия загрузчика."""

    version: str
    game_version: str
    stable: bool = True
    build: int = 0
    label: str = ""          # "recommended" / "latest" / ""
    note: str = ""

    @property
    def title(self) -> str:
        base = self.version
        if self.label:
            base += f"  ({self.label})"
        if not self.stable:
            base += "  (beta)"
        return base

    @property
    def sort_key(self) -> tuple:
        return _version_tuple(self.version)


def _version_tuple(text: str) -> tuple:
    import re

    parts = re.split(r"[.\-+]", text)
    result = []
    for part in parts:
        if part.isdigit():
            result.append(int(part))
        else:
            digits = re.findall(r"\d+", part)
            result.append(int(digits[0]) if digits else -1)
    return tuple(result)


@dataclass
class Loader:
    """Базовый загрузчик."""

    id: str
    title: str
    description: str = ""
    website: str = ""
    color: str = "#8b8b8b"
    supports_install: bool = True

    # ------------------------------------------------------------------
    def list_versions(
        self,
        paths: Paths,
        http: Http,
        game_version: str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> List[LoaderVersion]:
        raise NotImplementedError

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
        """Устанавливает загрузчик и возвращает id получившейся версии."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    def installed_versions(self, paths: Paths) -> List[str]:
        """Версии, чьи описания уже лежат локально."""
        repo = VersionRepository(paths)
        result = []
        for version_id in repo.list_local():
            raw = repo.load_raw(version_id)
            if self.matches(raw):
                result.append(version_id)
        return result

    def matches(self, version_json: Mapping[str, object]) -> bool:
        version_id = str(version_json.get("id") or "")
        return self.id in version_id.lower()


# ---------------------------------------------------------------------------
# Помощник запуска внешнего установщика (Forge / NeoForge)
# ---------------------------------------------------------------------------


def run_java_installer(
    paths: Paths,
    settings: Settings,
    http: Http,
    game_version: str,
    installer_path: Path,
    arguments: List[str],
    *,
    reporter: Optional[Reporter] = None,
    timeout: float = 1800.0,
) -> int:
    """Запускает installer jar, стримя вывод в журнал лаунчера."""
    required_java = 8
    try:
        repo = VersionRepository(paths, http)
        required_java = java_major_for(repo.resolve(game_version, reporter=reporter))
    except Exception:  # noqa: BLE001 - оставим значение по умолчанию
        pass

    runtime = ensure_java(
        paths,
        required_java,
        http,
        reporter=reporter,
        preferred_path=settings.java_path,
        allow_download=settings.auto_download_java,
    )

    # Установщикам Forge/NeoForge обязательно нужен launcher_profiles.json.
    write_launcher_profiles(paths.root / "launcher_profiles.json", game_version)

    argv = [str(runtime.path), "-jar", str(installer_path), *arguments]
    if reporter:
        reporter.log("Установщик: " + " ".join(argv))
        reporter.status("Запуск установщика загрузчика…")

    process = subprocess.Popen(
        argv,
        cwd=str(paths.root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    deadline = time.time() + timeout
    try:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            if reporter:
                reporter.check_cancel()
                text = line.rstrip()
                if text:
                    reporter.log(text)
            if time.time() > deadline:
                process.kill()
                raise LoaderError("Установщик загрузчика работает слишком долго")
        code = process.wait()
    except BaseException:
        if process.poll() is None:
            process.kill()
        raise
    finally:
        try:
            if process.stdout:
                process.stdout.close()
        except (OSError, ValueError):
            pass
    return code


def detect_installed_version(
    paths: Paths,
    installer_path: Path,
    started_at: float,
) -> Optional[str]:
    """Определяет id версии, созданной установщиком."""
    import zipfile

    # 1. Самый надёжный способ — заглянуть в install_profile.json.
    try:
        with zipfile.ZipFile(installer_path) as archive:
            if "install_profile.json" in archive.namelist():
                profile = json.loads(archive.read("install_profile.json"))
                version = profile.get("version")
                if version:
                    candidate = str(version)
                    if (paths.versions / candidate / f"{candidate}.json").is_file():
                        return candidate
                version_info = profile.get("versionInfo") or {}
                if version_info.get("id"):
                    candidate = str(version_info["id"])
                    if (paths.versions / candidate / f"{candidate}.json").is_file():
                        return candidate
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        pass

    # 2. Резерв: самый свежий каталог версии.
    newest: Optional[Path] = None
    newest_time = started_at
    if paths.versions.is_dir():
        for entry in paths.versions.iterdir():
            if not entry.is_dir():
                continue
            marker = entry / f"{entry.name}.json"
            if not marker.is_file():
                continue
            try:
                modified = marker.stat().st_mtime
            except OSError:
                continue
            if modified >= newest_time:
                newest = entry
                newest_time = modified
    return newest.name if newest else None


def prepare_for_installer(
    paths: Paths,
    settings: Settings,
    http: Http,
    game_version: str,
    *,
    reporter: Optional[Reporter] = None,
) -> None:
    """Готовит каталог к запуску установщика Forge/NeoForge.

    Установщику нужны ``launcher_profiles.json`` и ванильный клиент.
    """
    paths.ensure_base_dirs()
    write_launcher_profiles(paths.root / "launcher_profiles.json", game_version)
    repo = VersionRepository(paths, http)
    try:
        version = repo.resolve(game_version, reporter=reporter)
        ensure_client_jar(paths, game_version, version, http, reporter=reporter)
    except Exception as error:  # noqa: BLE001
        if reporter:
            reporter.log(f"Не удалось заранее подготовить клиент: {error}")
