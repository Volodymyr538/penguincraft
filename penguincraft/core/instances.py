"""Экземпляры (установки) игры.

Каждый экземпляр — это отдельный «домашний» каталог Minecraft со своими
модами, мирами и настройками, плюс собственная версия и загрузчик.
Так на одном хромбуке можно держать ваниль, Fabric-оптимизацию и
тяжёлый модпак, не мешая их друг другу.
"""

from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .paths import Paths
from .settings import InstanceConfig, Settings
from .util import copy_tree, extract_zip, remove_tree, sanitize_name

#: Каталоги, которые создаются в каждом экземпляре.
STANDARD_DIRS = (
    "mods",
    "config",
    "resourcepacks",
    "shaderpacks",
    "saves",
    "screenshots",
    "logs",
    "crash-reports",
)

#: Куда класть проекты Modrinth внутри экземпляра.
PROJECT_DIRS: Dict[str, str] = {
    "mod": "mods",
    "modpack": "mods",
    "resourcepack": "resourcepacks",
    "shader": "shaderpacks",
    "datapack": "datapacks",
}

#: Каталоги игры, которые не попадают в экспорт экземпляра.
EXPORT_EXCLUDE = ("logs", "crash-reports")


class InstanceError(Exception):
    """Ошибка работы с экземплярами."""


class InstanceManager:
    """Список и операции над экземплярами."""

    def __init__(self, paths: Paths, settings: Settings) -> None:
        self.paths = paths
        self.settings = settings

    # ------------------------------------------------------------------
    def list(self) -> List[InstanceConfig]:
        result: List[InstanceConfig] = []
        if not self.paths.instances.is_dir():
            return result
        for entry in sorted(self.paths.instances.iterdir()):
            if not entry.is_dir():
                continue
            config_path = entry / "instance.json"
            if config_path.is_file():
                config = InstanceConfig.load(config_path)
                if not config.name:
                    config.name = entry.name
                result.append(config)
            else:
                # Каталог без описания — показываем как «сырой» экземпляр.
                result.append(
                    InstanceConfig(
                        name=entry.name,
                        display_name=entry.name,
                        version_id="",
                        notes="Экземпляр без описания",
                    )
                )
        result.sort(key=lambda item: item.display_name.lower())
        return result

    def get(self, name: str) -> Optional[InstanceConfig]:
        for config in self.list():
            if config.name == name:
                return config
        return None

    def exists(self, name: str) -> bool:
        return self.paths.instance_dir(name).is_dir()

    # ------------------------------------------------------------------
    def unique_name(self, base: str) -> str:
        candidate = sanitize_name(base)
        if not self.exists(candidate):
            return candidate
        for number in range(2, 1000):
            candidate = sanitize_name(f"{base} ({number})")
            if not self.exists(candidate):
                return candidate
        raise InstanceError("Слишком много экземпляров с похожими именами")

    def create(
        self,
        display_name: str,
        version_id: str,
        *,
        loader: str = "vanilla",
        loader_version: str = "",
        game_version: str = "",
        icon: str = "🎮",
        notes: str = "",
        template: Optional[str] = None,
    ) -> InstanceConfig:
        """Создаёт новый экземпляр (каталоги и instance.json)."""
        name = self.unique_name(display_name)
        config = InstanceConfig(
            name=name,
            display_name=display_name.strip() or name,
            version_id=version_id,
            game_version=game_version or (version_id if loader == "vanilla" else ""),
            loader=loader,
            loader_version=loader_version,
            icon=icon or "🎮",
            notes=notes,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        game_dir = self.paths.instance_game_dir(name)
        if game_dir.exists():
            raise InstanceError(f"Каталог «{name}» уже существует")
        for directory in STANDARD_DIRS:
            (game_dir / directory).mkdir(parents=True, exist_ok=True)
        game_dir.mkdir(parents=True, exist_ok=True)
        if template:
            self._apply_template(Path(template), game_dir)
        self.save(config)
        return config

    def _apply_template(self, source: Path, game_dir: Path) -> None:
        mapping = {
            "mods": "mods",
            "config": "config",
            "resourcepacks": "resourcepacks",
            "shaderpacks": "shaderpacks",
        }
        for key, destination_name in mapping.items():
            src = source / key
            if src.is_dir():
                copy_tree(src, game_dir / destination_name)

    # ------------------------------------------------------------------
    def save(self, config: InstanceConfig) -> None:
        config.save(self.paths.instance_json(config.name))

    def delete(self, name: str, *, keep_mods: bool = False) -> None:
        directory = self.paths.instance_dir(name)
        if not directory.is_dir():
            raise InstanceError(f"Экземпляр «{name}» не найден")
        if keep_mods:
            backup = self.paths.cache / "deleted-instances" / f"{name}-{int(time.time())}"
            mods = directory / "game" / "mods"
            if mods.is_dir():
                backup.mkdir(parents=True, exist_ok=True)
                try:
                    copy_tree(mods, backup / "mods")
                except OSError:
                    pass
        remove_tree(directory)

    def clone(self, name: str, new_display_name: str = "") -> InstanceConfig:
        source = self.get(name)
        if source is None:
            raise InstanceError(f"Экземпляр «{name}» не найден")
        display = new_display_name.strip() or f"{source.display_name} (копия)"
        target_name = self.unique_name(display)
        source_dir = self.paths.instance_dir(name)
        target_dir = self.paths.instance_dir(target_name)
        shutil.copytree(source_dir, target_dir)
        clone = InstanceConfig.load(self.paths.instance_json(target_name))
        clone.name = target_name
        clone.display_name = display
        clone.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        clone.last_played = ""
        self.save(clone)
        return clone

    def rename(self, name: str, new_display_name: str) -> InstanceConfig:
        config = self.get(name)
        if config is None:
            raise InstanceError(f"Экземпляр «{name}» не найден")
        config.display_name = new_display_name.strip() or config.display_name
        self.save(config)
        return config

    # ------------------------------------------------------------------
    def game_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name)

    def mods_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "mods"

    def resourcepacks_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "resourcepacks"

    def shaderpacks_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "shaderpacks"

    def datapacks_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "datapacks"

    def saves_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "saves"

    def project_dir(self, name: str, project_type: str) -> Path:
        """Каталог для проекта Modrinth нужного типа (мод, шейдер и т.д.)."""
        kind = (project_type or "mod").lower().rstrip("s")
        folder = PROJECT_DIRS.get(kind, "mods")
        directory = self.paths.instance_game_dir(name) / folder
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def worlds(self, name: str) -> List[str]:
        """Названия миров экземпляра."""
        directory = self.saves_dir(name)
        if not directory.is_dir():
            return []
        return sorted(entry.name for entry in directory.iterdir() if entry.is_dir())

    def ensure_layout(self, name: str) -> Path:
        game_dir = self.paths.instance_game_dir(name)
        for directory in STANDARD_DIRS:
            (game_dir / directory).mkdir(parents=True, exist_ok=True)
        return game_dir

    def disk_usage(self, name: str) -> int:
        total = 0
        root = self.paths.instance_dir(name)
        if not root.is_dir():
            return 0
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    # ------------------------------------------------------------------
    # Экспорт, импорт и резервные копии
    # ------------------------------------------------------------------
    def export_archive(
        self,
        name: str,
        target: Path | str,
        *,
        include_game: bool = True,
        exclude: Iterable[str] = EXPORT_EXCLUDE,
        reporter=None,
    ) -> Path:
        """Складывает экземпляр в zip-архив (``instance.json`` + ``game/``)."""
        directory = self.paths.instance_dir(name)
        if not directory.is_dir():
            raise InstanceError(f"Экземпляр «{name}» не найден")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        excluded = tuple(exclude)
        tmp = target.with_name(target.name + ".part")
        count = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            config_path = self.paths.instance_json(name)
            if config_path.is_file():
                archive.write(config_path, "instance.json")
            if include_game:
                game = self.paths.instance_game_dir(name)
                if game.is_dir():
                    for path in sorted(game.rglob("*")):
                        if not path.is_file() or path.is_symlink():
                            continue
                        relative = path.relative_to(game)
                        if relative.parts and relative.parts[0] in excluded:
                            continue
                        archive.write(path, f"game/{relative.as_posix()}")
                        count += 1
                        if reporter and count % 50 == 0:
                            reporter.status(f"Экспорт: {count} файлов…")
                            reporter.check_cancel()
        tmp.replace(target)
        if reporter:
            reporter.log(f"Экспортировано файлов: {count}")
        return target

    def import_archive(
        self,
        archive_path: Path | str,
        *,
        display_name: str = "",
        reporter=None,
    ) -> InstanceConfig:
        """Создаёт новый экземпляр из архива, сделанного :meth:`export_archive`."""
        archive_path = Path(archive_path)
        if not archive_path.is_file():
            raise InstanceError(f"Архив не найден: {archive_path}")
        staging = self.paths.cache / "import" / f"{int(time.time())}-{sanitize_name(archive_path.stem)}"
        remove_tree(staging)
        try:
            if reporter:
                reporter.status("Распаковка архива…")
            extract_zip(archive_path, staging)
            config_path = staging / "instance.json"
            if not config_path.is_file():
                raise InstanceError(
                    "В архиве нет instance.json — это не экземпляр PenguinCraft."
                )
            source = InstanceConfig.load(config_path)
            wanted = display_name.strip() or source.display_name or archive_path.stem
            name = self.unique_name(wanted)
            target_dir = self.paths.instance_dir(name)
            target_dir.mkdir(parents=True, exist_ok=True)
            game_source = staging / "game"
            if game_source.is_dir():
                copy_tree(game_source, target_dir / "game")
            source.name = name
            source.display_name = wanted
            source.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            source.last_played = ""
            self.save(source)
            self.ensure_layout(name)
            return source
        finally:
            remove_tree(staging)

    def backup_worlds(
        self,
        name: str,
        target: Path | str,
        *,
        reporter=None,
    ) -> Path:
        """Сохраняет каталог ``saves`` экземпляра в zip-архив."""
        saves = self.saves_dir(name)
        if not saves.is_dir() or not any(saves.iterdir()):
            raise InstanceError("В экземпляре нет миров для резервной копии")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        count = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(saves.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(saves)
                archive.write(path, (Path("saves") / relative).as_posix())
                count += 1
                if reporter and count % 50 == 0:
                    reporter.status(f"Копирование миров: {count} файлов…")
                    reporter.check_cancel()
        tmp.replace(target)
        if reporter:
            reporter.log(f"Миров сохранено: {count} файлов")
        return target

    # ------------------------------------------------------------------
    def ensure_default(self) -> InstanceConfig:
        """Гарантирует, что есть хотя бы один экземпляр."""
        existing = self.list()
        if existing:
            return existing[0]
        try:
            from .mojang import VersionManifest

            manifest = VersionManifest(self.paths)
            latest = manifest.latest("release")
            version_id = latest.id if latest else "1.20.1"
        except Exception:  # noqa: BLE001 - офлайн
            version_id = "1.20.1"
        return self.create("По умолчанию", version_id)

    def touch_last_played(self, name: str) -> None:
        config = self.get(name)
        if config is None:
            return
        config.last_played = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save(config)

    # ------------------------------------------------------------------
    def options_txt(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "options.txt"

    def set_options(self, name: str, changes: Dict[str, str]) -> None:
        """Аккуратно правит ``options.txt`` (используется пресетами графики)."""
        path = self.options_txt(name)
        lines: List[str] = []
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        applied = set()
        result: List[str] = []
        for line in lines:
            key = line.split(":", 1)[0] if ":" in line else ""
            if key in changes:
                result.append(f"{key}:{changes[key]}")
                applied.add(key)
            else:
                result.append(line)
        for key, value in changes.items():
            if key not in applied:
                result.append(f"{key}:{value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(result) + "\n", encoding="utf-8")

    def export_list(self) -> List[Dict[str, object]]:
        """Компактное описание экземпляров (для интерфейса/экспорта)."""
        result = []
        for config in self.list():
            item = config.__dict__.copy()
            item["mods_count"] = len(list(self.mods_dir(config.name).glob("*.jar")))
            item["size"] = self.disk_usage(config.name)
            result.append(item)
        return result


# ---------------------------------------------------------------------------
# Пресеты графики для слабых машин
# ---------------------------------------------------------------------------

GRAPHICS_PRESETS: Dict[str, Dict[str, str]] = {
    "chromebook": {
        "graphicsMode": "0",
        "ao": "false",
        "renderDistance": "6",
        "simulationDistance": "6",
        "entityShadows": "false",
        "particles": "2",
        "graphics": "0",
        "clouds": "false",
        "fancyGraphics": "false",
        "enableVsync": "false",
        "maxFps": "60",
        "guiScale": "2",
        "bobView": "false",
        "screenEffectScale": "0.5",
        "entityDistanceScaling": "0.5",
        "weatherEffectScale": "0.5",
        "biomeBlendRadius": "0",
        "mipmapLevels": "2",
    },
    "balanced": {
        "renderDistance": "10",
        "simulationDistance": "10",
        "particles": "1",
        "clouds": "true",
        "graphicsMode": "1",
        "enableVsync": "false",
        "maxFps": "120",
        "biomeBlendRadius": "2",
    },
    "quality": {
        "renderDistance": "16",
        "simulationDistance": "12",
        "particles": "0",
        "graphicsMode": "1",
        "ao": "true",
        "entityShadows": "true",
        "biomeBlendRadius": "5",
    },
}
