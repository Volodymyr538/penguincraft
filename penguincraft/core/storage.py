"""Диск: размеры каталогов, очистка мусора и проверка свободного места.

Хромбуки живут с 32–64 ГБ накопителя, поэтому «сколько всё это занимает»
и «что можно безопасно удалить» — не любопытство, а необходимость.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .paths import Paths
from .util import remove_tree

#: Сколько дней журналы считаются старыми по умолчанию.
LOG_KEEP_DAYS = 7


class NotEnoughSpaceError(Exception):
    """На диске недостаточно места для операции."""

    def __init__(self, needed: int, free: int, where: str = "") -> None:
        self.needed = needed
        self.free = free
        self.where = where
        prefix = f"Недостаточно места для «{where}»: " if where else "Недостаточно места: "
        super().__init__(
            prefix
            + f"нужно примерно {format_size(needed)}, "
            + f"свободно {format_size(free)}. "
            + "Освободите место в разделе «Хранилище»."
        )


@dataclass
class StorageEntry:
    """Одна категория данных лаунчера."""

    key: str
    title: str
    path: Path
    size: int = 0
    files: int = 0
    note: str = ""
    removable: bool = False


@dataclass
class StorageReport:
    """Снимок занятого места."""

    root: Path
    entries: List[StorageEntry] = field(default_factory=list)
    instances: List[StorageEntry] = field(default_factory=list)
    unused_versions: List[str] = field(default_factory=list)
    unused_runtimes: List[str] = field(default_factory=list)
    total: int = 0
    free: int = 0
    disk_total: int = 0

    @property
    def used_by_launcher(self) -> int:
        return self.total

    @property
    def disk_used(self) -> int:
        return max(0, self.disk_total - self.free)


# ---------------------------------------------------------------------------
# Измерения
# ---------------------------------------------------------------------------


def format_size(num: float) -> str:
    """Человекочитаемый размер (без зависимости от UI)."""
    num = float(num)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(num) < 1024.0:
            return f"{int(num)} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} ПБ"


def measure(path: Path | str) -> Tuple[int, int]:
    """Возвращает ``(размер в байтах, число файлов)``."""
    target = Path(path)
    try:
        if target.is_file():
            return target.stat().st_size, 1
        if not target.is_dir():
            return 0, 0
    except OSError:
        return 0, 0

    size = 0
    files = 0
    for entry in target.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                size += entry.stat().st_size
                files += 1
        except OSError:
            continue
    return size, files


def free_space(path: Path | str) -> Tuple[int, int]:
    """Свободное и общее место на разделе, где лежит *path*."""
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return 0, 0
    return usage.free, usage.total


def ensure_free_space(
    path: Path | str,
    needed: int,
    *,
    what: str = "",
    margin: float = 0.10,
    floor: int = 0,
) -> None:
    """Проверяет, что после операции останется запас места.

    *margin* — доля запаса сверх оценки (файлы редко ложатся ровно),
    *floor* — минимальный порог, ниже которого операция не начинается.
    """
    required = max(int(floor), int(needed * (1.0 + max(0.0, margin))))
    free, _total = free_space(path)
    if free < required:
        raise NotEnoughSpaceError(required, free, what)


# ---------------------------------------------------------------------------
# Версии и JRE, на которые никто не ссылается
# ---------------------------------------------------------------------------


def _version_json(paths: Paths, version_id: str) -> Optional[dict]:
    candidate = paths.version_json(version_id)
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def local_version_ids(paths: Paths) -> List[str]:
    """Версии, у которых есть описание в ``versions/``."""
    if not paths.versions.is_dir():
        return []
    result: List[str] = []
    for entry in sorted(paths.versions.iterdir()):
        if entry.is_dir() and (entry / f"{entry.name}.json").is_file():
            result.append(entry.name)
    return result


def referenced_versions(paths: Paths, instances: Iterable[object]) -> List[str]:
    """Версии, нужные хотя бы одному экземпляру (с учётом ``inheritsFrom``)."""
    refs: set[str] = set()
    for instance in instances:
        for value in (
            getattr(instance, "version_id", ""),
            getattr(instance, "game_version", ""),
        ):
            if value:
                refs.add(str(value))
    # Loader-версия наследует ванильную — её тоже нельзя удалять.
    changed = True
    while changed:
        changed = False
        for version_id in list(refs):
            data = _version_json(paths, version_id)
            parent = str((data or {}).get("inheritsFrom") or "")
            if parent and parent not in refs:
                refs.add(parent)
                changed = True
    return sorted(refs)


def unused_versions(paths: Paths, instances: Iterable[object]) -> List[str]:
    """Версии, на которые не ссылается ни один экземпляр.

    Если экземпляров нет вовсе, возвращаем пустой список: удалять всё
    подряд из-за пустого списка экземпляров было бы слишком смело.
    """
    instance_list = list(instances)
    if not instance_list:
        return []
    used = set(referenced_versions(paths, instance_list))
    return [vid for vid in local_version_ids(paths) if vid not in used]


def unused_runtimes(paths: Paths, instances: Iterable[object]) -> List[str]:
    """Скачанные JRE, которые не нужны ни одной используемой версии."""
    if not paths.runtimes.is_dir():
        return []
    instance_list = list(instances)
    needed: set[str] = set()
    for version_id in referenced_versions(paths, instance_list):
        data = _version_json(paths, version_id) or {}
        java = data.get("javaVersion") or {}
        if isinstance(java, dict) and java.get("majorVersion"):
            needed.add(f"jre-{int(java['majorVersion'])}")
    # Экземпляр мог вручную указать Java из ``runtimes/`` — её не трогаем.
    try:
        root = paths.runtimes.resolve()
    except OSError:
        root = paths.runtimes
    for instance in instance_list:
        raw = str(getattr(instance, "java_path", "") or "")
        if not raw:
            continue
        try:
            relative = Path(raw).expanduser().resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if relative.parts:
            needed.add(relative.parts[0])
    if not needed:
        # Не смогли понять, какая Java нужна: лучше ничего не предлагать,
        # чем удалить единственную рабочую.
        return []
    result = []
    for entry in sorted(paths.runtimes.iterdir()):
        if entry.is_dir() and entry.name.startswith("jre-") and entry.name not in needed:
            result.append(entry.name)
    return result


# ---------------------------------------------------------------------------
# Снимок
# ---------------------------------------------------------------------------


def _entry(paths: Paths, key: str, title: str, path: Path, note: str = "",
           removable: bool = False) -> StorageEntry:
    size, files = measure(path)
    return StorageEntry(
        key=key, title=title, path=path, size=size, files=files,
        note=note, removable=removable,
    )


def _entry_many(paths: Paths, key: str, title: str, targets: Sequence[Path],
                note: str = "", removable: bool = False) -> StorageEntry:
    size = 0
    files = 0
    for target in targets:
        part_size, part_files = measure(target)
        size += part_size
        files += part_files
    return StorageEntry(
        key=key, title=title, path=targets[0] if targets else paths.cache,
        size=size, files=files, note=note, removable=removable,
    )


def scan(paths: Paths, instances: Sequence[object]) -> StorageReport:
    """Считает размеры всех крупных каталогов лаунчера."""
    report = StorageReport(root=paths.root)
    report.entries = [
        _entry(paths, "versions", "Версии и загрузчики", paths.versions,
               "описания версий, клиентские jar'ы, natives"),
        _entry(paths, "libraries", "Библиотеки", paths.libraries,
               "общие для всех экземпляров"),
        _entry(paths, "assets", "Ресурсы игры", paths.assets,
               "звуки, текстуры, языки — общие для всех версий"),
        _entry(paths, "runtimes", "Java (скачанная лаунчером)", paths.runtimes,
               "нужна для запуска игры", removable=True),
        _entry(paths, "meta", "Метаданные", paths.meta,
               "кеш манифестов Mojang и загрузчиков"),
        _entry(paths, "cache-downloads", "Кэш загрузок", paths.downloads,
               "недокачанные файлы и установщики", removable=True),
        _entry_many(
            paths, "cache-temp", "Временные распаковки",
            [paths.cache / "import", paths.cache / "world-import"],
            "остаются после импорта миров и экземпляров", removable=True,
        ),
        _entry(paths, "cache-updates", "Загруженные обновления",
               paths.cache / "updates",
               "установщики новой версии лаунчера", removable=True),
        _entry(paths, "cache-deleted", "Копии удалённых экземпляров",
               paths.cache / "deleted-instances",
               "моды, сохранённые при удалении", removable=True),
        _entry(paths, "logs", "Журналы лаунчера", paths.logs,
               "можно чистить без последствий", removable=True),
    ]

    for instance in instances:
        name = str(getattr(instance, "name", ""))
        title = str(getattr(instance, "display_name", "") or name)
        report.instances.append(
            _entry(paths, f"instance:{name}", title, paths.instance_dir(name))
        )

    report.entries.append(
        StorageEntry(
            key="instances",
            title="Экземпляры игры",
            path=paths.instances,
            size=sum(item.size for item in report.instances),
            files=sum(item.files for item in report.instances),
            note="моды, миры, настройки — по экземплярам ниже",
        )
    )

    report.total = sum(item.size for item in report.entries)
    report.unused_versions = unused_versions(paths, instances)
    report.unused_runtimes = unused_runtimes(paths, instances)
    report.free, report.disk_total = free_space(paths.root)
    return report


# ---------------------------------------------------------------------------
# Очистка
# ---------------------------------------------------------------------------


def _clear_directory(path: Path) -> int:
    """Удаляет содержимое каталога и возвращает освобождённые байты."""
    if not path.is_dir():
        return 0
    size, _files = measure(path)
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            remove_tree(entry)
        else:
            try:
                entry.unlink()
            except OSError:
                pass
    return size


def clean_downloads(paths: Paths) -> int:
    """Чистит кэш загрузок (недокачанные ``.part`` тоже)."""
    size = _clear_directory(paths.downloads)
    paths.downloads.mkdir(parents=True, exist_ok=True)
    return size


def clean_temp(paths: Paths) -> int:
    """Чистит временные распаковки импорта."""
    freed = 0
    for name in ("import", "world-import"):
        freed += _clear_directory(paths.cache / name)
    return freed


def clean_cache(paths: Paths, *, include_updates: bool = True) -> int:
    """Чистит весь безопасный кэш: загрузки, распаковки, установщики."""
    freed = clean_downloads(paths) + clean_temp(paths)
    if include_updates:
        freed += _clear_directory(paths.cache / "updates")
    return freed


def clean_deleted_backups(paths: Paths) -> int:
    """Удаляет копии модов от удалённых экземпляров."""
    return _clear_directory(paths.cache / "deleted-instances")


def clean_logs(paths: Paths, *, days: int = LOG_KEEP_DAYS) -> Tuple[int, int]:
    """Удаляет журналы старше *days* дней.  Возвращает (байты, файлов)."""
    if not paths.logs.is_dir():
        return 0, 0
    cutoff = time.time() - max(0, int(days)) * 86400
    freed = 0
    removed = 0
    for entry in paths.logs.rglob("*"):
        try:
            if not entry.is_file() or entry.is_symlink():
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
            freed += entry.stat().st_size
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return freed, removed


def clean_unused_versions(paths: Paths, version_ids: Sequence[str]) -> int:
    """Удаляет перечисленные версии.  Возвращает освобождённые байты."""
    freed = 0
    for version_id in version_ids:
        directory = paths.version_dir(version_id)
        if not directory.is_dir():
            continue
        size, _files = measure(directory)
        remove_tree(directory)
        freed += size
    return freed


def clean_unused_runtimes(paths: Paths, names: Sequence[str]) -> int:
    """Удаляет перечисленные каталоги ``runtimes/jre-*``."""
    freed = 0
    for name in names:
        directory = paths.runtimes / name
        if not directory.is_dir():
            continue
        size, _files = measure(directory)
        remove_tree(directory)
        freed += size
    return freed
