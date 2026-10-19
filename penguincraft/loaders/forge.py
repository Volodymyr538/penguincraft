"""Forge и NeoForge.

Оба загрузчика ставятся официальным installer-jar'ом, поэтому сложную
работу (сборка патченного клиента, процессоры) делает сам установщик.
Лаунчер лишь готовит каталог и передаёт правильный флаг.

* Forge:    ``java -jar forge-…-installer.jar --installClient <каталог>``
* NeoForge: ``java -jar neoforge-…-installer.jar --install-client <каталог>``
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ElementTree
from typing import Dict, List, Optional

from ..core.net import Http
from ..core.paths import Paths
from ..core.settings import Settings
from ..core.task import Reporter
from .base import (
    Loader,
    LoaderError,
    LoaderVersion,
    detect_installed_version,
    prepare_for_installer,
    run_java_installer,
)


class MavenLoader(Loader):
    """Загрузчик, публикующий установщики в Maven-репозитории."""

    maven_base: str = ""
    maven_group_path: str = ""
    installer_arguments: List[str] = []
    #: Как выбрать каталог для установки (подставляется место ``{}``).
    installer_dir_flag: str = "--installClient"

    # ------------------------------------------------------------------
    @property
    def metadata_url(self) -> str:
        return f"{self.maven_base.rstrip('/')}/{self.maven_group_path}/maven-metadata.xml"

    def _versions(self, paths: Paths, http: Http, reporter: Optional[Reporter]) -> List[str]:
        cache = paths.meta / f"{self.id}-versions.json"
        if cache.is_file():
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                if time.time() - float(payload.get("_ts", 0)) < 6 * 3600:
                    return list(payload.get("versions", []))
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        if reporter:
            reporter.status(f"Получение версий {self.title}…")
        try:
            raw = http.get_bytes(self.metadata_url)
        except Exception as error:  # noqa: BLE001
            if cache.is_file():
                try:
                    return list(json.loads(cache.read_text(encoding="utf-8")).get("versions", []))
                except (OSError, json.JSONDecodeError):
                    pass
            raise LoaderError(f"Не удалось получить список версий {self.title}: {error}") from error
        try:
            root = ElementTree.fromstring(raw)
            versions = [
                element.text or ""
                for element in root.iter("version")
                if element.text
            ]
        except ElementTree.ParseError as error:
            raise LoaderError(f"Некорректный список версий {self.title}") from error
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"_ts": time.time(), "versions": versions}), encoding="utf-8"
        )
        return versions

    # -- сопоставление версий ---------------------------------------------
    def game_version_of(self, maven_version: str) -> str:
        raise NotImplementedError

    def filter_for_game(self, versions: List[str], game_version: str) -> List[LoaderVersion]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    def list_versions(
        self,
        paths: Paths,
        http: Http,
        game_version: str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> List[LoaderVersion]:
        versions = self._versions(paths, http, reporter)
        result = self.filter_for_game(versions, game_version)
        result.sort(key=lambda item: item.sort_key, reverse=True)
        return result

    # ------------------------------------------------------------------
    def installer_url(self, loader_version: str) -> str:
        return (
            f"{self.maven_base.rstrip('/')}/{self.maven_group_path}/"
            f"{loader_version}/{self.installer_artifact.format(version=loader_version)}"
        )

    @property
    def installer_artifact(self) -> str:
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
        paths.ensure_base_dirs()
        url = self.installer_url(loader_version)
        installer_path = paths.downloads / url.rsplit("/", 1)[-1]
        if reporter:
            reporter.status(f"Загрузка установщика {self.title} {loader_version}…")
        http.download(url, installer_path, reporter=reporter)

        prepare_for_installer(paths, settings, http, game_version, reporter=reporter)

        arguments = [self.installer_dir_flag, str(paths.root)]
        started = time.time()
        code = run_java_installer(
            paths,
            settings,
            http,
            game_version,
            installer_path,
            arguments,
            reporter=reporter,
        )
        if code != 0:
            raise LoaderError(
                f"Установщик {self.title} завершился с кодом {code}. "
                "Подробности — в журнале."
            )

        version_id = detect_installed_version(paths, installer_path, started)
        if not version_id:
            raise LoaderError(
                f"{self.title} установился, но лаунчер не нашёл файл версии. "
                "Проверьте журнал установки."
            )
        try:
            installer_path.unlink()
        except OSError:
            pass
        if reporter:
            reporter.log(f"{self.title} установлен: {version_id}")
        return version_id


# ---------------------------------------------------------------------------
# Forge
# ---------------------------------------------------------------------------


class ForgeLoader(MavenLoader):
    def __init__(self) -> None:
        super().__init__(
            id="forge",
            title="Forge",
            description=(
                "Самый совместимый загрузчик: большинство модпаков (в том "
                "числе крупные сборки) рассчитаны именно на него. "
                "Требует больше ресурсов, чем Fabric."
            ),
            website="https://files.minecraftforge.net",
            color="#7a8ba3",
        )
        self.maven_base = "https://maven.minecraftforge.net"
        self.maven_group_path = "net/minecraftforge/forge"
        self.installer_dir_flag = "--installClient"
        self._promotions: Optional[Dict[str, str]] = None

    @property
    def installer_artifact(self) -> str:
        return "forge-{version}-installer.jar"

    def game_version_of(self, maven_version: str) -> str:
        match = re.match(r"^(\d+\.\d+(?:\.\d+)?)-", maven_version)
        return match.group(1) if match else ""

    def promotions(self, http: Http) -> Dict[str, str]:
        if self._promotions is not None:
            return self._promotions
        try:
            data = http.get_json(
                "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
            )
            self._promotions = dict(data.get("promos") or {}) if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            self._promotions = {}
        return self._promotions

    def filter_for_game(self, versions: List[str], game_version: str) -> List[LoaderVersion]:
        prefix = f"{game_version}-"
        result: List[LoaderVersion] = []
        for version in versions:
            if not version.startswith(prefix):
                continue
            forge_part = version[len(prefix):]
            result.append(
                LoaderVersion(
                    version=version,
                    game_version=game_version,
                    stable=True,
                    build=0,
                    note=forge_part,
                )
            )
        return result


# ---------------------------------------------------------------------------
# NeoForge
# ---------------------------------------------------------------------------


class NeoForgeLoader(MavenLoader):
    def __init__(self) -> None:
        super().__init__(
            id="neoforge",
            title="NeoForge",
            description=(
                "Современный форк Forge. Обязателен для модпаков и модов "
                "новых версий Minecraft (1.20.4+)."
            ),
            website="https://neoforged.net",
            color="#f7962f",
        )
        self.maven_base = "https://maven.neoforged.net/releases"
        self.maven_group_path = "net/neoforged/neoforge"
        self.installer_dir_flag = "--install-client"

    @property
    def installer_artifact(self) -> str:
        return "neoforge-{version}-installer.jar"

    def game_version_of(self, maven_version: str) -> str:
        if maven_version.startswith("1."):
            # Старая схема: 1.20.1-47.1.106
            return maven_version.split("-", 1)[0]
        parts = maven_version.split(".")
        if len(parts) < 2:
            return ""
        try:
            major = int(parts[0])
            minor = int(parts[1])
        except ValueError:
            return ""
        if major >= 26:  # новая нумерация Minecraft
            return f"{major}.{minor}"
        return f"1.{major}" + (f".{minor}" if minor else "")

    def filter_for_game(self, versions: List[str], game_version: str) -> List[LoaderVersion]:
        result: List[LoaderVersion] = []
        for version in versions:
            if self.game_version_of(version) != game_version:
                continue
            result.append(
                LoaderVersion(
                    version=version,
                    game_version=game_version,
                    stable=True,
                    note=version,
                )
            )
        return result
