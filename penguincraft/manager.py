"""Высокоуровневый фасад лаунчера.

Собирает вместе настройки, аккаунты, экземпляры, загрузчики, моды и
установку.  Именно этот слой используют и графический интерфейс, и
консольный режим.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from . import APP_NAME, APP_VERSION
from .auth.account import Account, AccountType
from .auth.microsoft import MicrosoftAuth, MicrosoftAuthError
from .auth.store import AccountStore
from .auth.yggdrasil import AUTH_SERVERS, YggdrasilAuth, YggdrasilError, AuthServer, ELYBY
from .core.instances import GRAPHICS_PRESETS, InstanceManager
from .core.installer import InstallError, InstallPlan, install_version
from .core.java import JavaRuntime, ensure_java, find_installed_java
from .core.launch import GameProcess, LaunchCommand, LaunchError, build_command, spawn
from .core.mojang import VersionManifest, VersionRepository, java_major_for
from .core.net import Http
from .core.paths import Paths
from .core.session import SessionError, ensure_valid_account
from .core.settings import InstanceConfig, JVM_PRESETS, Settings, auto_memory_mb
from .core.task import Cancelled, Reporter
from .loaders import Loader, LoaderError, get_loader, loader_for_version, local_versions
from .mods.modrinth import Modrinth, ModrinthError
from .mods.mrpack import ModpackError, detect_modpack_kind, install_modpack, read_index
from .mods import manager as mod_manager


class LauncherError(Exception):
    """Общая ошибка лаунчера, показываемая пользователю."""


class LauncherManager:
    """Точка входа для всех операций лаунчера."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.paths = Paths(root)
        self.paths.ensure_base_dirs()
        self.settings = Settings.load(self.paths)
        self.http = Http(proxy=self.settings.proxy or None)
        self.accounts = AccountStore(self.paths.accounts)
        self.instances = InstanceManager(self.paths, self.settings)
        self.repository = VersionRepository(self.paths, self.http)
        self.manifest: VersionManifest = self.repository.manifest
        self.modrinth = Modrinth(self.http)
        self.process: Optional[GameProcess] = None

    # ------------------------------------------------------------------
    # Общие операции
    # ------------------------------------------------------------------
    def save_settings(self) -> None:
        self.settings.sanitize()
        self.settings.save(self.paths)

    def reload_http(self) -> None:
        self.http = Http(proxy=self.settings.proxy or None)
        self.repository = VersionRepository(self.paths, self.http)
        self.manifest = self.repository.manifest
        self.modrinth = Modrinth(self.http)

    def java_runtimes(self) -> List[JavaRuntime]:
        return find_installed_java(self.paths)

    def game_versions(
        self,
        *,
        snapshots: Optional[bool] = None,
        old: Optional[bool] = None,
        force: bool = False,
        reporter: Optional[Reporter] = None,
    ) -> List[Any]:
        return self.manifest.versions(
            include_snapshots=self.settings.show_snapshots if snapshots is None else snapshots,
            include_old=self.settings.show_old_versions if old is None else old,
            force=force,
        )

    # ------------------------------------------------------------------
    # Аккаунты
    # ------------------------------------------------------------------
    def add_offline_account(self, name: str) -> Account:
        from .auth.offline import make_offline_account

        account = make_offline_account(name)
        return self.accounts.add(account)

    def add_elyby_account(
        self,
        username: str,
        password: str,
        server: AuthServer = ELYBY,
        *,
        reporter: Optional[Reporter] = None,
    ) -> Account:
        auth = YggdrasilAuth(self.http)
        try:
            account = auth.authenticate(server, username, password)
        except YggdrasilError as error:
            raise LauncherError(str(error)) from error
        return self.accounts.add(account)

    def start_microsoft_login(
        self,
        *,
        reporter: Optional[Reporter] = None,
        on_prompt: Optional[Callable[[Any], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Account:
        auth = MicrosoftAuth(self.http)
        try:
            account = auth.authenticate(
                reporter=reporter, on_prompt=on_prompt, on_status=on_status
            )
        except MicrosoftAuthError as error:
            raise LauncherError(str(error)) from error
        return self.accounts.add(account)

    def refresh_account(self, account: Account, reporter: Optional[Reporter] = None) -> Account:
        try:
            fresh = ensure_valid_account(account, self.http, reporter=reporter)
        except SessionError as error:
            raise LauncherError(str(error)) from error
        if fresh is not account:
            self.accounts.update(fresh)
        return fresh

    # ------------------------------------------------------------------
    # Установка
    # ------------------------------------------------------------------
    def resolve_version_id(self, instance: InstanceConfig, reporter: Optional[Reporter] = None) -> str:
        """Гарантирует наличие описания версии и возвращает её id."""
        if not instance.version_id and not instance.game_version:
            raise LauncherError("У экземпляра не выбрана версия Minecraft")

        if instance.version_id and self.repository.has_local(instance.version_id):
            return instance.version_id

        game_version = instance.game_version or instance.version_id
        loader = get_loader(instance.loader or "vanilla")

        loader_version = instance.loader_version
        if loader.id != "vanilla" and not loader_version:
            loader_version = self._pick_loader_version(loader, game_version, reporter)
            instance.loader_version = loader_version

        if reporter:
            reporter.status(f"Подготовка {loader.title}…")
        try:
            version_id = loader.install(
                self.paths,
                self.settings,
                self.http,
                game_version,
                loader_version,
                reporter=reporter,
            )
        except LoaderError as error:
            raise LauncherError(str(error)) from error
        except Exception as error:  # noqa: BLE001
            raise LauncherError(f"Не удалось установить {loader.title}: {error}") from error

        instance.version_id = version_id
        if not instance.game_version:
            instance.game_version = game_version
        self.instances.save(instance)
        return version_id

    def install_instance(
        self,
        instance: InstanceConfig,
        *,
        reporter: Optional[Reporter] = None,
        download_assets: bool = True,
    ) -> InstallPlan:
        """Устанавливает/допроверяет всё нужное для запуска экземпляра."""
        version_id = self.resolve_version_id(instance, reporter)
        game_dir = self.instances.ensure_layout(instance.name)
        try:
            return install_version(
                self.paths,
                self.settings,
                version_id,
                self.http,
                reporter=reporter,
                game_dir=game_dir,
                repository=self.repository,
                java_path=instance.java_path or self.settings.java_path,
                allow_java_download=self.settings.auto_download_java,
                download_assets=download_assets,
            )
        except InstallError as error:
            raise LauncherError(str(error)) from error
        except Cancelled:
            raise
        except Exception as error:  # noqa: BLE001
            raise LauncherError(f"Ошибка установки: {error}") from error

    def _pick_loader_version(
        self,
        loader: Loader,
        game_version: str,
        reporter: Optional[Reporter] = None,
    ) -> str:
        """Выбирает последнюю стабильную версию загрузчика."""
        try:
            versions = loader.list_versions(
                self.paths, self.http, game_version, reporter=reporter
            )
        except Exception as error:  # noqa: BLE001
            raise LauncherError(
                f"Не удалось получить список версий {loader.title}: {error}"
            ) from error
        if not versions:
            raise LauncherError(
                f"{loader.title} не поддерживает Minecraft {game_version}"
            )
        for item in versions:
            if item.stable:
                return item.version
        return versions[0].version

    def install_version_only(
        self,
        game_version: str,
        loader: str = "vanilla",
        loader_version: str = "",
        *,
        reporter: Optional[Reporter] = None,
    ) -> str:
        """Ставит версию/загрузчик без создания экземпляра."""
        target = get_loader(loader)
        if target.id != "vanilla" and not loader_version:
            loader_version = self._pick_loader_version(target, game_version, reporter)
        try:
            return target.install(
                self.paths,
                self.settings,
                self.http,
                game_version,
                loader_version,
                reporter=reporter,
            )
        except LoaderError as error:
            raise LauncherError(str(error)) from error

    # ------------------------------------------------------------------
    # Запуск
    # ------------------------------------------------------------------
    def launch(
        self,
        instance: InstanceConfig,
        account: Optional[Account],
        *,
        reporter: Optional[Reporter] = None,
        on_line: Optional[Callable[[str], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None,
        install: bool = True,
    ) -> GameProcess:
        """Готовит всё и запускает игру."""
        if self.process is not None and self.process.running:
            raise LauncherError(
                f"Игра уже запущена (PID {self.process.pid}). Остановите её сначала."
            )
        if account is None:
            account = self.accounts.active
        if account is None:
            raise LauncherError("Сначала добавьте аккаунт на вкладке «Аккаунты»")

        account = self.refresh_account(account, reporter)

        plan = (
            self.install_instance(instance, reporter=reporter)
            if install
            else self.install_instance(instance, reporter=reporter, download_assets=False)
        )
        try:
            command = build_command(
                self.paths, self.settings, instance, account, plan
            )
        except LaunchError as error:
            raise LauncherError(str(error)) from error

        if reporter:
            reporter.status("Запуск Minecraft…")
            reporter.log(f"Java: {plan.java.title if plan.java else '?'}")
            reporter.log(f"Память: {instance.memory_for(self.settings)} МБ")
            reporter.log(f"Каталог игры: {plan.game_dir}")

        try:
            process = spawn(command)
        except LaunchError as error:
            raise LauncherError(str(error)) from error
        process.on_line = on_line
        process.on_exit = on_exit
        self.process = process
        self.instances.touch_last_played(instance.name)
        return process

    def prepare_command(
        self,
        instance: InstanceConfig,
        account: Optional[Account] = None,
    ) -> LaunchCommand:
        """Собирает команду запуска без старта процесса (страница «Консоль»)."""
        plan = self.install_instance(instance)
        account = account or self.accounts.active
        return build_command(self.paths, self.settings, instance, account, plan)

    @property
    def game_running(self) -> bool:
        return self.process is not None and self.process.running

    def stop_game(self) -> None:
        if self.process is not None:
            self.process.stop()

    # ------------------------------------------------------------------
    # Моды
    # ------------------------------------------------------------------
    def list_mods(self, instance: InstanceConfig):
        return mod_manager.list_mods(self.instances.mods_dir(instance.name))

    def install_mod_from_modrinth(
        self,
        instance: InstanceConfig,
        project_id: str,
        *,
        version_id: str = "",
        reporter: Optional[Reporter] = None,
    ) -> Path:
        """Ставит мод с Modrinth в каталог экземпляра."""
        mods_dir = self.instances.mods_dir(instance.name)
        mods_dir.mkdir(parents=True, exist_ok=True)
        if version_id:
            version = self.modrinth.version(version_id)
        else:
            version = self.modrinth.best_version(
                project_id,
                loader=instance.loader,
                game_version=instance.game_version or instance.version_id,
            )
        if version is None:
            raise LauncherError("Не найдена подходящая версия мода")
        file = version.primary_file
        if file is None:
            raise LauncherError("У версии мода нет файлов для скачивания")
        if reporter:
            reporter.status(f"Загрузка {file.filename}…")
        return self.modrinth.download_file(file, mods_dir, reporter=reporter)

    def install_modpack_file(
        self,
        archive: Path | str,
        *,
        display_name: str = "",
        reporter: Optional[Reporter] = None,
    ) -> Tuple[InstanceConfig, Any]:
        kind = detect_modpack_kind(archive)
        if kind == "curseforge":
            raise LauncherError(
                "Модпаки CurseForge пока не поддерживаются напрямую. "
                "Скачайте версию для Modrinth (.mrpack) или установите моды по одному."
            )
        if kind != "mrpack":
            raise LauncherError(
                "Не удалось распознать формат модпака. Ожидается .mrpack от Modrinth."
            )
        try:
            return install_modpack(
                self.paths,
                self.settings,
                self.instances,
                self.http,
                archive,
                display_name=display_name,
                reporter=reporter,
            )
        except ModpackError as error:
            raise LauncherError(str(error)) from error

    def download_modpack(
        self,
        project_id: str,
        *,
        version_id: str = "",
        game_version: str = "",
        loader: str = "",
        reporter: Optional[Reporter] = None,
    ) -> Tuple[InstanceConfig, Any]:
        """Скачивает и ставит модпак прямо с Modrinth."""
        if not version_id:
            version = self.modrinth.best_version(
                project_id, loader=loader, game_version=game_version
            )
            if version is None:
                raise LauncherError("Не найдена подходящая версия модпака")
        else:
            version = self.modrinth.version(version_id)
            if version is None:
                raise LauncherError("Версия модпака не найдена")
        file = version.primary_file
        if file is None:
            raise LauncherError("У модпака нет файла для скачивания")
        self.paths.downloads.mkdir(parents=True, exist_ok=True)
        target = self.paths.downloads / file.filename
        if reporter:
            reporter.status(f"Загрузка модпака {file.filename}…")
        self.modrinth.download_file(file, target, reporter=reporter)
        return self.install_modpack_file(
            target, display_name=version.name or file.filename, reporter=reporter
        )

    # ------------------------------------------------------------------
    # Прочее
    # ------------------------------------------------------------------
    def apply_graphics_preset(self, instance: InstanceConfig, preset: str) -> None:
        changes = GRAPHICS_PRESETS.get(preset)
        if not changes:
            raise LauncherError(f"Неизвестный пресет графики: {preset}")
        self.instances.set_options(instance.name, dict(changes))

    def instance_summary(self) -> List[Dict[str, Any]]:
        return self.instances.export_list()

    def loader_for(self, version_id: str) -> Loader:
        return loader_for_version(version_id)

    def local_version_list(self):
        return local_versions(self.paths)

    def memory_suggestion(self, preset: str = "") -> int:
        return auto_memory_mb(preset or self.settings.jvm_preset)
