"""Высокоуровневый фасад лаунчера.

Собирает вместе настройки, аккаунты, экземпляры, загрузчики, моды и
установку.  Именно этот слой используют и графический интерфейс, и
консольный режим.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .auth.account import Account
from .auth.microsoft import MicrosoftAuth, MicrosoftAuthError
from .auth.store import AccountStore
from .auth.yggdrasil import YggdrasilAuth, YggdrasilError, AuthServer, ELYBY
from .core.instances import GRAPHICS_PRESETS, InstanceManager, WorldInfo
from .core import diagnostics as diagnostics_module
from .core import storage as storage_module
from .core.installer import InstallError, InstallPlan, install_version
from .core.integrity import IntegrityReport, repair_version
from .core.java import JavaRuntime, find_installed_java
from .core.launch import GameProcess, LaunchCommand, LaunchError, build_command, spawn
from .core.mojang import VersionManifest, VersionRepository
from .core.net import Http
from .core.paths import Paths
from .core.session import SessionError, ensure_valid_account
from .core.settings import InstanceConfig, Settings, auto_memory_mb
from .core.task import Cancelled, Reporter
from .core.update import (
    ReleaseInfo,
    UpdateAsset,
    check_latest,
    download_update,
    pick_asset,
)
from .loaders import Loader, LoaderError, get_loader, loader_for_version, local_versions
from .mods import dependencies as mod_dependencies
from .mods import presets as mod_presets
from .mods import share as mod_share
from .mods.modrinth import Modrinth, ModVersion
from .mods.mrpack import (
    ModpackError,
    detect_modpack_kind,
    install_modpack,
    update_modpack,
)
from .mods.index import InstalledIndex, InstalledProject
from .mods import manager as mod_manager


class LauncherError(Exception):
    """Общая ошибка лаунчера, показываемая пользователю."""


class LauncherManager:
    """Точка входа для всех операций лаунчера."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.paths = Paths(root)
        self.paths.ensure_base_dirs()
        # Экземпляры, созданные до 1.4.0, хранили каталог игры как ``game/``.
        # Переносим его в привычный ``.minecraft/`` — это обычное
        # переименование, файлы остаются на месте.
        self.paths.migrate_legacy_game_dirs()
        self.settings = Settings.load(self.paths)
        self.http = Http(
            proxy=self.settings.proxy or None,
            limit_kbps=self.settings.download_limit_kbps,
        )
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
        self.http = Http(
            proxy=self.settings.proxy or None,
            limit_kbps=self.settings.download_limit_kbps,
        )
        self.repository = VersionRepository(self.paths, self.http)
        self.manifest = self.repository.manifest
        self.modrinth = Modrinth(self.http)

    def _ensure_space(self, needed: int, what: str, *, floor: int = 0) -> None:
        """Не начинает операцию, если диск заведомо переполнится."""
        try:
            storage_module.ensure_free_space(
                self.paths.root, needed, what=what, floor=floor
            )
        except storage_module.NotEnoughSpaceError as error:
            raise LauncherError(str(error)) from error

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

    def install_project_from_modrinth(
        self,
        instance: InstanceConfig,
        project_id: str,
        *,
        project_type: str = "mod",
        version_id: str = "",
        reporter: Optional[Reporter] = None,
    ) -> Path:
        """Ставит проект Modrinth в нужный каталог экземпляра.

        Моды идут в ``mods``, ресурспаки — в ``resourcepacks``,
        шейдеры — в ``shaderpacks``.
        """
        kind = (project_type or "mod").lower()
        target_dir = self.instances.project_dir(instance.name, kind)
        loader = instance.loader if kind == "mod" else ""
        if version_id:
            version = self.modrinth.version(version_id)
        else:
            version = self.modrinth.best_version(
                project_id,
                loader=loader,
                game_version=instance.game_version or instance.version_id,
            )
        if version is None:
            raise LauncherError("Не найдена подходящая версия проекта")
        file = version.primary_file
        if file is None:
            raise LauncherError("У версии проекта нет файлов для скачивания")
        if reporter:
            reporter.status(f"Загрузка {file.filename}…")
        path = self.modrinth.download_file(file, target_dir, reporter=reporter)
        requires = [
            str(item.get("project_id") or "")
            for item in version.dependencies or []
            if isinstance(item, dict)
            and str(item.get("dependency_type") or "required") == "required"
            and item.get("project_id")
        ]
        incompatible = [
            str(item.get("project_id") or "")
            for item in version.dependencies or []
            if isinstance(item, dict)
            and str(item.get("dependency_type") or "") == "incompatible"
            and item.get("project_id")
        ]
        try:
            index = self._index(instance)
            index.record(
                path.name,
                project_id=project_id,
                version_id=version.id,
                version_number=version.version_number,
                title=version.name or path.name,
                project_type=kind,
                requires=requires,
                incompatible=incompatible,
            )
            index.save()
        except OSError:
            pass
        return path

    def install_project_with_dependencies(
        self,
        instance: InstanceConfig,
        project_id: str,
        *,
        project_type: str = "mod",
        version_id: str = "",
        with_dependencies: Optional[bool] = None,
        reporter: Optional[Reporter] = None,
    ) -> Tuple[Path, List[Path]]:
        """Ставит проект и (по настройке) его обязательные зависимости.

        Возвращает ``(основной файл, файлы зависимостей)``.
        """
        kind = (project_type or "mod").lower()
        loader = instance.loader if kind == "mod" else ""
        game_version = instance.game_version or instance.version_id
        if version_id:
            version = self.modrinth.version(version_id)
        else:
            version = self.modrinth.best_version(
                project_id, loader=loader, game_version=game_version
            )
        if version is None:
            raise LauncherError("Не найдена подходящая версия проекта")

        want_deps = (
            self.settings.install_dependencies
            if with_dependencies is None
            else with_dependencies
        )
        installed_ids = [record.project_id for record in self.installed_index(instance).items()]
        dep_paths: List[Path] = []
        if want_deps and kind == "mod":
            plan = mod_dependencies.resolve_for_version(
                self.modrinth,
                version,
                loader=loader,
                game_version=game_version,
                installed=installed_ids,
            )
            total = len(plan.required)
            for number, dependency in enumerate(plan.required, start=1):
                if reporter:
                    reporter.status(
                        f"Зависимость {number}/{total}: "
                        f"{dependency.version.name or dependency.project_id}…"
                    )
                    reporter.check_cancel()
                try:
                    path = self.install_project_from_modrinth(
                        instance,
                        dependency.project_id,
                        project_type="mod",
                        version_id=dependency.version.id,
                        reporter=reporter,
                    )
                except LauncherError as error:
                    if reporter:
                        reporter.log(
                            f"Не удалось поставить зависимость "
                            f"{dependency.version.name or dependency.project_id}: {error}"
                        )
                    continue
                dep_paths.append(path)
                if reporter:
                    reporter.log(f"Зависимость установлена: {path.name}")
            if reporter and plan.unresolved:
                reporter.log(
                    "Не найдены версии зависимостей: " + ", ".join(plan.unresolved)
                )
            if reporter and plan.conflicts:
                reporter.log(
                    "Конфликты с установленными модами: " + ", ".join(plan.conflicts)
                )

        main = self.install_project_from_modrinth(
            instance,
            project_id,
            project_type=kind,
            version_id=version.id,
            reporter=reporter,
        )
        return main, dep_paths

    # ------------------------------------------------------------------
    # Индекс установленного и обновления
    # ------------------------------------------------------------------
    def _index(self, instance: InstanceConfig) -> InstalledIndex:
        return InstalledIndex(self.paths.instance_index(instance.name)).load()

    def installed_index(self, instance: InstanceConfig) -> InstalledIndex:
        """Индекс установленных проектов с чисткой записей о пропавших файлах."""
        index = self._index(instance)
        suffix = mod_manager.DISABLED_SUFFIX
        existing: List[str] = []
        for kind in ("mod", "resourcepack", "shader"):
            directory = self.instances.project_dir(instance.name, kind)
            for entry in directory.iterdir():
                if not entry.is_file():
                    continue
                existing.append(entry.name)
                if entry.name.endswith(suffix):
                    existing.append(entry.name[: -len(suffix)])
        if index.prune(existing):
            try:
                index.save()
            except OSError:
                pass
        return index

    def check_project_updates(
        self, instance: InstanceConfig
    ) -> List[Tuple[InstalledProject, ModVersion]]:
        """Ищет более свежие версии установленных проектов."""
        index = self.installed_index(instance)
        game_version = instance.game_version or instance.version_id
        found: List[Tuple[InstalledProject, ModVersion]] = []
        for record in index.items():
            if not record.project_id:
                continue
            loader = instance.loader if record.project_type == "mod" else ""
            latest = self.modrinth.best_version(
                record.project_id, loader=loader, game_version=game_version
            )
            if latest is None or not latest.id:
                continue
            if latest.id != record.version_id:
                found.append((record, latest))
        return found

    def update_project(
        self,
        instance: InstanceConfig,
        record: InstalledProject,
        *,
        version: Optional[ModVersion] = None,
        reporter: Optional[Reporter] = None,
    ) -> Path:
        """Обновляет один установленный проект до свежей версии."""
        loader = instance.loader if record.project_type == "mod" else ""
        if version is None:
            version = self.modrinth.best_version(
                record.project_id,
                loader=loader,
                game_version=instance.game_version or instance.version_id,
            )
        if version is None:
            raise LauncherError(
                f"Не найдена новая версия «{record.title or record.filename}»"
            )
        file = version.primary_file
        if file is None:
            raise LauncherError("У новой версии нет файлов для скачивания")
        target_dir = self.instances.project_dir(instance.name, record.project_type)
        suffix = mod_manager.DISABLED_SUFFIX
        old = target_dir / record.filename
        disabled = False
        if not old.is_file() and not record.filename.endswith(suffix):
            candidate = target_dir / (record.filename + suffix)
            if candidate.is_file():
                old = candidate
                disabled = True
        if reporter:
            reporter.status(f"Обновление {file.filename}…")
        path = self.modrinth.download_file(file, target_dir, reporter=reporter)
        if old.is_file() and old != path:
            try:
                old.unlink()
            except OSError:
                pass
        if disabled and not path.name.endswith(suffix):
            target = path.with_name(path.name + suffix)
            try:
                path.rename(target)
                path = target
            except OSError:
                pass
        base_name = path.name[: -len(suffix)] if path.name.endswith(suffix) else path.name
        try:
            index = self._index(instance)
            index.forget([record.filename])
            index.record(
                base_name,
                project_id=record.project_id,
                version_id=version.id,
                version_number=version.version_number,
                title=version.name or base_name,
                project_type=record.project_type,
                requires=[
                    str(item.get("project_id") or "")
                    for item in version.dependencies or []
                    if isinstance(item, dict)
                    and str(item.get("dependency_type") or "required") == "required"
                    and item.get("project_id")
                ],
                incompatible=[
                    str(item.get("project_id") or "")
                    for item in version.dependencies or []
                    if isinstance(item, dict)
                    and str(item.get("dependency_type") or "") == "incompatible"
                    and item.get("project_id")
                ],
            )
            index.save()
        except OSError:
            pass
        return path

    # ------------------------------------------------------------------
    # Ручные моды: сопоставление с Modrinth и автообновление
    # ------------------------------------------------------------------
    def match_manual_mods(
        self,
        instance: InstanceConfig,
        *,
        reporter: Optional[Reporter] = None,
        include_resourcepacks: bool = False,
    ) -> Dict[str, str]:
        """Сопоставляет локальные файлы с Modrinth по sha1.

        Заодно пополняет ``installed.json``: после этого для вручную
        поставленных модов работают проверка и установка обновлений.
        Возвращает ``имя файла → project_id`` для найденных.
        """
        from .core.util import hash_file

        index = self.installed_index(instance)
        known = {record.filename for record in index.items()}
        kinds = ["mod", "resourcepack", "shader"] if include_resourcepacks else ["mod"]
        found: Dict[str, str] = {}
        candidates: List[Tuple[str, Path, str]] = []
        for kind in kinds:
            directory = self.instances.project_dir(instance.name, kind)
            for entry in sorted(directory.iterdir()):
                if not entry.is_file():
                    continue
                base = (
                    entry.name[: -len(mod_manager.DISABLED_SUFFIX)]
                    if entry.name.endswith(mod_manager.DISABLED_SUFFIX)
                    else entry.name
                )
                if base in known or base in found:
                    continue
                if Path(base).suffix.lower() not in (".jar", ".zip", ".litemod"):
                    continue
                candidates.append((kind, entry, base))

        total = len(candidates)
        for number, (kind, entry, base) in enumerate(candidates, start=1):
            if reporter:
                reporter.status(f"Сопоставление с Modrinth: {number}/{total}…")
                reporter.check_cancel()
            try:
                digest = hash_file(entry, "sha1")
            except OSError:
                continue
            version = self.modrinth.version_by_hash(digest)
            if version is None:
                continue
            project_id = version.project_id
            if not project_id:
                continue
            found[base] = project_id
            index.record(
                base,
                project_id=project_id,
                version_id=version.id,
                version_number=version.version_number,
                title=version.name or base,
                project_type=kind,
                requires=[
                    str(item.get("project_id") or "")
                    for item in version.dependencies or []
                    if isinstance(item, dict)
                    and str(item.get("dependency_type") or "required") == "required"
                    and item.get("project_id")
                ],
                incompatible=[
                    str(item.get("project_id") or "")
                    for item in version.dependencies or []
                    if isinstance(item, dict)
                    and str(item.get("dependency_type") or "") == "incompatible"
                    and item.get("project_id")
                ],
            )
            if reporter:
                reporter.log(f"Найден в Modrinth: {base} → {version.name or project_id}")
        try:
            index.save()
        except OSError:
            pass
        return found

    def auto_update_mods(
        self,
        *,
        reporter: Optional[Reporter] = None,
        force: bool = False,
        match_manual: bool = True,
    ) -> Dict[str, int]:
        """Обновляет проекты во всех экземплярах (если включено в настройках).

        Возвращает счётчики: ``instances``, ``updated``, ``failed``,
        ``checked``.  При ``force=False`` уважает интервал
        ``auto_update_hours`` и отметку ``last_mod_update_check``.
        """
        import time as _time

        settings = self.settings
        now = _time.time()
        interval = max(1, int(settings.auto_update_hours or 24)) * 3600
        if not force and settings.last_mod_update_check:
            if now - settings.last_mod_update_check < interval:
                return {"instances": 0, "updated": 0, "failed": 0, "checked": 0}

        counters = {"instances": 0, "updated": 0, "failed": 0, "checked": 0}
        instances = self.instances.list()
        for instance in instances:
            if reporter:
                reporter.check_cancel()
                reporter.status(f"Проверка обновлений: {instance.display_name}")
            counters["instances"] += 1
            try:
                if match_manual:
                    self.match_manual_mods(instance, reporter=reporter)
                found = self.check_project_updates(instance)
            except Exception as error:  # noqa: BLE001 - один экземпляр не должен ломать остальные
                counters["failed"] += 1
                if reporter:
                    reporter.log(
                        f"Не удалось проверить «{instance.display_name}»: {error}"
                    )
                continue
            counters["checked"] += 1
            for record, version in found:
                if reporter:
                    reporter.check_cancel()
                try:
                    path = self.update_project(
                        instance, record, version=version, reporter=reporter
                    )
                except Exception as error:  # noqa: BLE001
                    counters["failed"] += 1
                    if reporter:
                        reporter.log(
                            f"Не удалось обновить «{record.title or record.filename}»: {error}"
                        )
                    continue
                counters["updated"] += 1
                if reporter:
                    reporter.log(
                        f"Обновлён «{record.title or record.filename}» → {path.name}"
                    )
        settings.last_mod_update_check = now
        try:
            self.save_settings()
        except OSError:
            pass
        return counters

    def check_instance_health(
        self, instance: InstanceConfig
    ) -> Dict[str, List[str]]:
        """Проверяет зависимости и совместимость локальных модов.

        Возвращает словарь с ключами ``missing`` (нет зависимостей),
        ``disabled`` (зависимость выключена), ``conflicts`` (конфликт по
        индексу) и ``loaders`` (мод другого загрузчика).
        """
        mods = mod_manager.list_mods(self.instances.mods_dir(instance.name))
        result: Dict[str, List[str]] = {
            "missing": [],
            "disabled": [],
            "conflicts": [],
            "loaders": [],
        }
        for title, deps in mod_dependencies.missing_local(mods).items():
            result["missing"].append(f"{title}: {', '.join(deps)}")
        for title, deps in mod_dependencies.disabled_requirements(mods).items():
            result["disabled"].append(f"{title}: {', '.join(deps)}")

        index = self.installed_index(instance)
        installed = {record.project_id for record in index.items() if record.project_id}
        for record in index.items():
            for conflict in record.incompatible:
                if conflict in installed and conflict != record.project_id:
                    result["conflicts"].append(
                        f"{record.title or record.filename} ↔ {conflict}"
                    )
        loader = instance.loader
        if loader and loader != "vanilla":
            aliases = {"quilt": {"quilt", "fabric"}, "neoforge": {"neoforge", "forge"}}
            allowed = aliases.get(loader, {loader})
            for mod in mods:
                if mod.loader and mod.loader not in allowed:
                    result["loaders"].append(
                        f"{mod.title or mod.filename}: {mod.loader} (нужен {loader})"
                    )
        return {key: value for key, value in result.items() if value}

    # ------------------------------------------------------------------
    # Целостность файлов
    # ------------------------------------------------------------------
    def verify_instance(
        self,
        instance: InstanceConfig,
        *,
        deep: bool = False,
        repair: bool = False,
        reporter: Optional[Reporter] = None,
    ) -> IntegrityReport:
        """Проверяет файлы версии экземпляра и при необходимости чинит их."""
        version_id = instance.version_id
        if not version_id:
            raise LauncherError("У экземпляра не выбрана версия — проверять нечего")
        if repair:
            return repair_version(
                self.paths,
                self.settings,
                version_id,
                self.http,
                reporter=reporter,
                game_dir=self.instances.game_dir(instance.name),
                repository=self.repository,
                deep=deep,
                java_path=instance.java_path,
            )
        version = self.repository.resolve(version_id, reporter=reporter)
        from .core.integrity import check_version

        return check_version(
            self.paths, version, version_id, deep=deep, reporter=reporter
        )

    # ------------------------------------------------------------------
    # Обновления лаунчера
    # ------------------------------------------------------------------
    def check_launcher_update(self, *, raise_errors: bool = False) -> Optional[ReleaseInfo]:
        """Проверяет GitHub Releases (``None`` при ошибке сети)."""
        return check_latest(self.http, raise_errors=raise_errors)

    def launcher_update_asset(
        self,
        release: ReleaseInfo,
        *,
        portable: bool = False,
        platform: str = "",
    ) -> Optional[UpdateAsset]:
        """Подбирает файл обновления под текущую систему."""
        return pick_asset(release, platform=platform, portable=portable)

    def download_launcher_update(
        self,
        asset: UpdateAsset,
        *,
        reporter: Optional[Reporter] = None,
    ) -> Path:
        """Скачивает установщик новой версии в кэш обновлений."""
        return download_update(
            self.http, asset, self.paths.cache / "updates", reporter=reporter
        )

    # ------------------------------------------------------------------
    # Хранилище
    # ------------------------------------------------------------------
    def storage_report(self) -> storage_module.StorageReport:
        """Считает, сколько места занимают версии, моды, миры и кэш."""
        return storage_module.scan(self.paths, self.instances.list())

    def clean_storage(
        self,
        *,
        cache: bool = False,
        logs: bool = False,
        versions: bool = False,
        runtimes: bool = False,
        backups: bool = False,
        days: int = storage_module.LOG_KEEP_DAYS,
        reporter: Optional[Reporter] = None,
    ) -> Dict[str, int]:
        """Чистит выбранные категории.  Возвращает освобождённые байты."""
        freed: Dict[str, int] = {}
        if reporter:
            reporter.status("Очистка хранилища…")
            reporter.check_cancel()
        if cache:
            freed["cache"] = storage_module.clean_cache(self.paths)
        if logs:
            size, _count = storage_module.clean_logs(self.paths, days=days)
            freed["logs"] = size
        if backups:
            freed["backups"] = storage_module.clean_deleted_backups(self.paths)
        report = None
        if versions or runtimes:
            report = storage_module.scan(self.paths, self.instances.list())
        if versions and report is not None:
            freed["versions"] = storage_module.clean_unused_versions(
                self.paths, report.unused_versions
            )
        if runtimes and report is not None:
            freed["runtimes"] = storage_module.clean_unused_runtimes(
                self.paths, report.unused_runtimes
            )
        if reporter:
            total = sum(freed.values())
            reporter.log(f"Освобождено: {storage_module.format_size(total)}")
        return freed

    def free_space(self) -> Tuple[int, int]:
        """Свободное и общее место на разделе с данными лаунчера."""
        return storage_module.free_space(self.paths.root)

    # ------------------------------------------------------------------
    # Диагностика
    # ------------------------------------------------------------------
    def export_diagnostics(
        self,
        target: Path | str = "",
        *,
        reporter: Optional[Reporter] = None,
    ) -> Path:
        """Собирает zip с отчётом, журналами и отчётами о падениях."""
        destination = Path(target) if target else (
            Path.home() / diagnostics_module.default_name()
        )
        return diagnostics_module.export_diagnostics(
            self, destination, reporter=reporter
        )

    # ------------------------------------------------------------------
    # Списки модов
    # ------------------------------------------------------------------
    def export_mod_list(
        self,
        instance: InstanceConfig,
        target: Path | str,
        *,
        fmt: str = "md",
    ) -> Path:
        """Сохраняет список модов экземпляра (``md`` или ``json``)."""
        return mod_share.export_mod_list(self, instance, target, fmt=fmt)

    def compare_instances(
        self, left: InstanceConfig, right: InstanceConfig
    ) -> mod_share.Comparison:
        """Сравнивает наборы модов двух экземпляров."""
        return mod_share.compare(self, left, right)

    # ------------------------------------------------------------------
    # Пресет оптимизации
    # ------------------------------------------------------------------
    def optimization_projects(self, instance: InstanceConfig) -> List[Any]:
        return mod_presets.projects_for(instance.loader)

    def install_optimization_preset(
        self,
        instance: InstanceConfig,
        *,
        with_dependencies: Optional[bool] = None,
        reporter: Optional[Reporter] = None,
    ) -> Dict[str, List[str]]:
        """Ставит набор оптимизационных модов, подходящих экземпляру.

        Возвращает три списка: ``installed``, ``skipped`` и ``failed``.
        Недоступные для этой версии игры моды пропускаются, а не ломают
        всю установку.
        """
        result: Dict[str, List[str]] = {"installed": [], "skipped": [], "failed": []}
        loader = (instance.loader or "vanilla").lower()
        if loader == "vanilla":
            raise LauncherError(
                "Оптимизационные моды требуют Fabric, Quilt, Forge или NeoForge. "
                "Создайте модовый экземпляр или установите загрузчик."
            )
        projects = mod_presets.projects_for(loader)
        if not projects:
            raise LauncherError(f"Для загрузчика «{loader}» пресет не подобран.")

        game_version = instance.game_version or instance.version_id
        installed = {
            record.project_id
            for record in self.installed_index(instance).items()
            if record.project_id
        }
        for number, item in enumerate(projects, start=1):
            if reporter:
                reporter.status(
                    f"Оптимизация {number}/{len(projects)}: {item.title}…"
                )
                reporter.check_cancel()
            try:
                project = self.modrinth.project(item.slug)
            except Exception as error:  # noqa: BLE001 - сеть/404
                result["failed"].append(f"{item.title}: {error}")
                if reporter:
                    reporter.log(f"Оптимизация: {item.title} — {error}")
                continue
            project_id = str((project or {}).get("id") or "")
            if not project_id:
                result["failed"].append(f"{item.title}: проект не найден на Modrinth")
                continue
            if project_id in installed:
                result["skipped"].append(f"{item.title} — уже установлен")
                continue
            try:
                version = self.modrinth.best_version(
                    project_id, loader=loader, game_version=game_version
                )
            except Exception as error:  # noqa: BLE001
                result["failed"].append(f"{item.title}: {error}")
                continue
            if version is None:
                result["skipped"].append(
                    f"{item.title} — нет версии для {game_version}"
                )
                continue
            try:
                self.install_project_with_dependencies(
                    instance,
                    project_id,
                    project_type="mod",
                    version_id=version.id,
                    with_dependencies=with_dependencies,
                    reporter=reporter,
                )
            except Cancelled:
                raise
            except Exception as error:  # noqa: BLE001 - один мод не должен ронять всё
                result["failed"].append(f"{item.title}: {error}")
                if reporter:
                    reporter.log(f"Оптимизация: {item.title} — {error}")
                continue
            installed.add(project_id)
            result["installed"].append(item.title)
            if reporter:
                reporter.log(f"Оптимизация: установлен {item.title}")
        return result

    # ------------------------------------------------------------------
    # Миры
    # ------------------------------------------------------------------
    def worlds(self, instance: InstanceConfig) -> List[WorldInfo]:
        return self.instances.world_infos(instance.name)

    def export_world(
        self,
        instance: InstanceConfig,
        world: str,
        target: Path | str,
        *,
        reporter: Optional[Reporter] = None,
    ) -> Path:
        return self.instances.export_world(instance.name, world, target, reporter=reporter)

    def import_world(
        self,
        instance: InstanceConfig,
        archive: Path | str,
        *,
        world_name: str = "",
        reporter: Optional[Reporter] = None,
    ) -> WorldInfo:
        try:
            archive_size = Path(archive).stat().st_size
        except OSError:
            archive_size = 0
        if archive_size >= 8 * 1024 * 1024:
            # Мир распаковывается в кэш и копируется в экземпляр — берём запас.
            self._ensure_space(
                archive_size * 8, "импорт мира", floor=64 * 1024 * 1024
            )
        return self.instances.import_world(
            instance.name, archive, world_name=world_name, reporter=reporter
        )

    def delete_world(self, instance: InstanceConfig, world: str) -> None:
        self.instances.delete_world(instance.name, world)

    # ------------------------------------------------------------------
    # Модпаки
    # ------------------------------------------------------------------
    def record_modpack_source(
        self,
        instance: InstanceConfig,
        *,
        project_id: str = "",
        version_id: str = "",
        version_number: str = "",
        title: str = "",
        slug: str = "",
        files: Optional[List[str]] = None,
    ) -> None:
        """Запоминает, из какого проекта Modrinth поставлен модпак."""
        try:
            index = self._index(instance)
            index.set_pack(
                project_id=project_id,
                version_id=version_id,
                version_number=version_number,
                title=title or instance.display_name,
                project_slug=slug,
                files=list(files or []),
            )
            index.save()
        except OSError:
            pass

    def check_modpack_update(
        self, instance: InstanceConfig
    ) -> Optional[Tuple[InstalledProject, ModVersion]]:
        """Есть ли более свежая версия у модпака, из которого сделан экземпляр."""
        record = self._index(instance).pack
        if record is None or not record.project_id:
            return None
        latest = self.modrinth.best_version(
            record.project_id,
            loader=instance.loader,
            game_version=instance.game_version or instance.version_id,
        )
        if latest is None or not latest.id or latest.id == record.version_id:
            return None
        return record, latest

    def update_modpack(
        self,
        instance: InstanceConfig,
        *,
        version_id: str = "",
        reporter: Optional[Reporter] = None,
    ) -> Tuple[InstanceConfig, Any]:
        """Обновляет экземпляр новым ``.mrpack`` того же модпака (на месте)."""
        record = self._index(instance).pack
        if record is None or not record.project_id:
            raise LauncherError(
                "Источник модпака не записан — обновление невозможно. "
                "Экземпляр создан из файла или старой версией лаунчера."
            )
        if version_id:
            version = self.modrinth.version(version_id)
        else:
            version = self.modrinth.best_version(
                record.project_id,
                loader=instance.loader,
                game_version=instance.game_version or instance.version_id,
            )
        if version is None:
            raise LauncherError("Не найдена новая версия модпака")
        file = version.primary_file
        if file is None:
            raise LauncherError("У версии модпака нет файла для скачивания")
        self.paths.downloads.mkdir(parents=True, exist_ok=True)
        target = self.paths.downloads / file.filename
        if reporter:
            reporter.status(f"Загрузка модпака {file.filename}…")
        self.modrinth.download_file(file, target, reporter=reporter)
        pack_index = update_modpack(
            self.paths,
            self.settings,
            self.instances,
            self.http,
            instance,
            target,
            old_files=record.files,
            reporter=reporter,
        )
        self.record_modpack_source(
            instance,
            project_id=record.project_id,
            version_id=version.id,
            version_number=version.version_number,
            title=version.name or record.title,
            slug=record.project_slug,
            files=[item.path for item in pack_index.client_files],
        )
        return instance, pack_index

    # ------------------------------------------------------------------
    # Экспорт, импорт и резервные копии
    # ------------------------------------------------------------------
    def export_instance(
        self, instance: InstanceConfig, target: Path | str, *, reporter: Optional[Reporter] = None
    ) -> Path:
        return self.instances.export_archive(instance.name, target, reporter=reporter)

    def import_instance(
        self, archive: Path | str, *, display_name: str = "", reporter: Optional[Reporter] = None
    ) -> InstanceConfig:
        return self.instances.import_archive(archive, display_name=display_name, reporter=reporter)

    def backup_worlds(
        self, instance: InstanceConfig, target: Path | str, *, reporter: Optional[Reporter] = None
    ) -> Path:
        return self.instances.backup_worlds(instance.name, target, reporter=reporter)

    def instance_worlds(self, instance: InstanceConfig) -> List[str]:
        return self.instances.worlds(instance.name)

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
            archive_size = Path(archive).stat().st_size
        except OSError:
            archive_size = 0
        if archive_size >= 32 * 1024 * 1024:
            self._ensure_space(
                archive_size * 4, "установку модпака", floor=128 * 1024 * 1024
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
        instance, pack_index = self.install_modpack_file(
            target, display_name=version.name or file.filename, reporter=reporter
        )
        self.record_modpack_source(
            instance,
            project_id=project_id,
            version_id=version.id,
            version_number=version.version_number,
            title=version.name or file.filename,
            files=[item.path for item in pack_index.client_files],
        )
        return instance, pack_index

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
