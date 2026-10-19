"""Экземпляры (установки) игры.

Каждый экземпляр — это отдельный «домашний» каталог Minecraft со своими
модами, мирами и настройками, плюс собственная версия и загрузчик.
Так на одном хромбуке можно держать ваниль, Fabric-оптимизацию и
тяжёлый модпак, не мешая их друг другу.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .paths import Paths
from .settings import InstanceConfig, Settings
from .util import copy_tree, remove_tree, sanitize_name

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

    def saves_dir(self, name: str) -> Path:
        return self.paths.instance_game_dir(name) / "saves"

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
