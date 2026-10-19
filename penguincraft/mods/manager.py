"""Управление локальными модами в каталоге ``mods``."""

from __future__ import annotations

import json
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DISABLED_SUFFIX = ".disabled"


@dataclass
class LocalMod:
    """Мод, лежащий в каталоге ``mods``."""

    path: Path
    enabled: bool = True
    name: str = ""
    mod_id: str = ""
    version: str = ""
    description: str = ""
    authors: List[str] = field(default_factory=list)
    loader: str = ""
    error: str = ""

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        if self.name:
            return self.name
        return self.path.name[: -len(DISABLED_SUFFIX)] if self.path.name.endswith(DISABLED_SUFFIX) else self.path.name

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def modified(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    @property
    def subtitle(self) -> str:
        parts = []
        if self.mod_id:
            parts.append(self.mod_id)
        if self.version:
            parts.append(self.version)
        if self.loader:
            parts.append(self.loader)
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# Разбор метаданных
# ---------------------------------------------------------------------------


def _read_entry(archive: zipfile.ZipFile, name: str) -> Optional[bytes]:
    try:
        return archive.read(name)
    except (KeyError, OSError):
        return None


def parse_mod_metadata(path: Path | str) -> Dict[str, Any]:
    """Достаёт метаданные мода из jar-файла (или папки)."""
    target = Path(path)
    result: Dict[str, Any] = {"loader": "", "name": "", "id": "", "version": "", "description": "", "authors": []}
    if target.is_dir():
        return result
    try:
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()

            # Fabric
            if "fabric.mod.json" in names:
                raw = _read_entry(archive, "fabric.mod.json")
                if raw:
                    return _parse_fabric_json(raw, "fabric")
            # Quilt
            if "quilt.mod.json" in names:
                raw = _read_entry(archive, "quilt.mod.json")
                if raw:
                    return _parse_quilt_json(raw)
            # Forge / NeoForge
            for candidate, loader in (
                ("META-INF/neoforge.mods.toml", "neoforge"),
                ("META-INF/mods.toml", "forge"),
            ):
                if candidate in names:
                    raw = _read_entry(archive, candidate)
                    if raw:
                        parsed = _parse_toml_mods(raw, loader)
                        if parsed.get("id") or parsed.get("name"):
                            return parsed

            # Совсем старые Forge: mcmod.info
            if "mcmod.info" in names:
                raw = _read_entry(archive, "mcmod.info")
                if raw:
                    parsed = _parse_mcmod_info(raw)
                    if parsed:
                        return parsed
    except (zipfile.BadZipFile, OSError) as error:
        result["error"] = f"Повреждённый архив: {error}"
    return result


def _parse_fabric_json(raw: bytes, loader: str) -> Dict[str, Any]:
    data = json.loads(raw.decode("utf-8", "replace"))
    authors = data.get("authors") or []
    if isinstance(authors, list) and authors and isinstance(authors[0], Mapping):
        authors = [str(item.get("name") or "") for item in authors if isinstance(item, Mapping)]
    return {
        "loader": loader,
        "name": str(data.get("name") or data.get("id") or ""),
        "id": str(data.get("id") or ""),
        "version": str(data.get("version") or ""),
        "description": str(data.get("description") or ""),
        "authors": [str(item) for item in authors if item],
    }


def _parse_quilt_json(raw: bytes) -> Dict[str, Any]:
    data = json.loads(raw.decode("utf-8", "replace"))
    loader_block = data.get("quilt_loader") or {}
    metadata = loader_block.get("metadata") or {}
    contributors = metadata.get("contributors") or {}
    authors = list(contributors.values()) if isinstance(contributors, Mapping) else []
    return {
        "loader": "quilt",
        "name": str(metadata.get("name") or loader_block.get("id") or ""),
        "id": str(loader_block.get("id") or ""),
        "version": str(loader_block.get("version") or ""),
        "description": str(metadata.get("description") or ""),
        "authors": [str(item) for item in authors if item],
    }


_TOML_MODID = re.compile(r'^\s*modId\s*=\s*"([^"]*)"', re.MULTILINE)
_TOML_DISPLAY = re.compile(r'^\s*displayName\s*=\s*"([^"]*)"', re.MULTILINE)
_TOML_VERSION = re.compile(r'^\s*version\s*=\s*"([^"]*)"', re.MULTILINE)
_TOML_DESC = re.compile(r'^\s*description\s*=\s*"([^"]*)"', re.MULTILINE)


def _parse_toml_mods(raw: bytes, loader: str) -> Dict[str, Any]:
    text = raw.decode("utf-8", "replace")
    data: Dict[str, Any] = {}
    try:
        import tomllib  # Python 3.11+

        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001 - Python 3.9/3.10 или нестандартный TOML
        data = {}

    mods = data.get("mods") if isinstance(data, dict) else None
    if isinstance(mods, list) and mods:
        first = mods[0]
        if isinstance(first, Mapping):
            raw_authors = first.get("authors") or ""
            return {
                "loader": loader,
                "name": str(first.get("displayName") or first.get("modId") or ""),
                "id": str(first.get("modId") or ""),
                "version": str(first.get("version") or ""),
                "description": str(first.get("description") or ""),
                "authors": [item.strip() for item in str(raw_authors).split(",") if item.strip()],
            }

    # Ручной разбор
    return {
        "loader": loader,
        "name": (_TOML_DISPLAY.search(text) or _TOML_MODID.search(text)).group(1) if (_TOML_DISPLAY.search(text) or _TOML_MODID.search(text)) else "",
        "id": _TOML_MODID.search(text).group(1) if _TOML_MODID.search(text) else "",
        "version": _TOML_VERSION.search(text).group(1) if _TOML_VERSION.search(text) else "",
        "description": _TOML_DESC.search(text).group(1) if _TOML_DESC.search(text) else "",
        "authors": [],
    }


def _parse_mcmod_info(raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"//.*", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    entry = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    if not isinstance(entry, Mapping):
        return {}
    authors = entry.get("authorList") or ([entry.get("author")] if entry.get("author") else [])
    return {
        "loader": "forge",
        "name": str(entry.get("name") or ""),
        "id": str(entry.get("modid") or ""),
        "version": str(entry.get("version") or ""),
        "description": str(entry.get("description") or ""),
        "authors": [str(item) for item in authors if item],
    }


# ---------------------------------------------------------------------------
# Список и операции
# ---------------------------------------------------------------------------


def list_mods(mods_dir: Path | str) -> List[LocalMod]:
    """Все моды в каталоге, включая отключённые."""
    directory = Path(mods_dir)
    result: List[LocalMod] = []
    if not directory.is_dir():
        return result
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in (".jar", ".zip", ".litemod") and not entry.name.endswith(".jar" + DISABLED_SUFFIX):
            if not entry.name.endswith(DISABLED_SUFFIX):
                continue
        enabled = not entry.name.endswith(DISABLED_SUFFIX)
        if entry.name.endswith(DISABLED_SUFFIX):
            stem = entry.name[: -len(DISABLED_SUFFIX)]
            if Path(stem).suffix.lower() not in (".jar", ".zip", ".litemod"):
                continue
        metadata = parse_mod_metadata(entry)
        result.append(
            LocalMod(
                path=entry,
                enabled=enabled,
                name=str(metadata.get("name") or ""),
                mod_id=str(metadata.get("id") or ""),
                version=str(metadata.get("version") or ""),
                description=str(metadata.get("description") or ""),
                authors=[str(item) for item in (metadata.get("authors") or [])],
                loader=str(metadata.get("loader") or ""),
                error=str(metadata.get("error") or ""),
            )
        )
    result.sort(key=lambda item: item.title.lower())
    return result


def set_enabled(mod: LocalMod, enabled: bool) -> Path:
    """Включает/выключает мод переименованием в ``*.disabled``."""
    current = mod.path
    name = current.name
    if enabled:
        if name.endswith(DISABLED_SUFFIX):
            target = current.with_name(name[: -len(DISABLED_SUFFIX)])
        else:
            return current
    else:
        if name.endswith(DISABLED_SUFFIX):
            return current
        target = current.with_name(name + DISABLED_SUFFIX)
    if target.exists():
        raise FileExistsError(f"Файл уже существует: {target.name}")
    current.rename(target)
    return target


def delete_mod(mod: LocalMod) -> None:
    try:
        mod.path.unlink()
    except FileNotFoundError:
        pass


def import_mods(
    sources: Iterable[Path | str],
    mods_dir: Path | str,
    *,
    overwrite: bool = False,
) -> Tuple[List[Path], List[str]]:
    """Копирует файлы модов в каталог. Возвращает (скопированные, ошибки)."""
    directory = Path(mods_dir)
    directory.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []
    errors: List[str] = []
    for source in sources:
        src = Path(source)
        try:
            if not src.is_file():
                errors.append(f"{src.name}: не файл")
                continue
            if src.suffix.lower() not in (".jar", ".zip", ".litemod"):
                errors.append(f"{src.name}: неподдерживаемое расширение")
                continue
            target = directory / src.name
            if target.exists() and not overwrite:
                stem = target.stem
                suffix = target.suffix
                for number in range(2, 1000):
                    candidate = directory / f"{stem} ({number}){suffix}"
                    if not candidate.exists():
                        target = candidate
                        break
            shutil.copy2(src, target)
            copied.append(target)
        except OSError as error:
            errors.append(f"{src.name}: {error}")
    return copied, errors


def find_duplicates(mods_dir: Path | str) -> Dict[str, List[Path]]:
    """Ищет моды с одинаковым ``mod_id`` и раскидывает их по группам."""
    groups: Dict[str, List[Path]] = {}
    for mod in list_mods(mods_dir):
        key = mod.mod_id or mod.path.stem.split("-")[0]
        if not key:
            continue
        groups.setdefault(key.lower(), []).append(mod.path)
    return {key: value for key, value in groups.items() if len(value) > 1}


def folder_summary(mods_dir: Path | str) -> Dict[str, Any]:
    """Сводка по каталогу модов."""
    mods = list_mods(mods_dir)
    return {
        "total": len(mods),
        "enabled": sum(1 for item in mods if item.enabled),
        "disabled": sum(1 for item in mods if not item.enabled),
        "size": sum(item.size for item in mods),
        "duplicates": find_duplicates(mods_dir),
    }
