"""Зависимости модов: подбор с Modrinth и проверка локальных jar-файлов.

Две независимые задачи:

* :func:`resolve_for_version` — по версии проекта Modrinth находит
  обязательные зависимости, которые ещё не установлены;
* :func:`missing_local` — по метаданным лежащих в ``mods`` файлов ищет
  зависимости, которых нет в каталоге (работает офлайн).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .manager import LocalMod
from .modrinth import ModVersion, Modrinth

#: Идентификаторы, которые «есть всегда» и не требуют установки.
BUILTIN_IDS: Set[str] = {
    "minecraft",
    "java",
    "fabricloader",
    "fabric-loader",
    "fabric_loader",
    "quilt_loader",
    "quilted_fabric_api",
    "forge",
    "neoforge",
    "javafml",
    "lowcodefml",
    "mixinextras",
}

#: Сколько уровней зависимостей разворачивать.
MAX_DEPTH = 4


@dataclass
class ResolvedDependency:
    """Найденная зависимость, готовая к установке."""

    project_id: str
    version: ModVersion
    #: Откуда узнали: имя родительского проекта.
    parent: str = ""


@dataclass
class DependencyPlan:
    """Результат разбора зависимостей версии Modrinth."""

    required: List[ResolvedDependency] = field(default_factory=list)
    optional: List[ResolvedDependency] = field(default_factory=list)
    #: Установленные проекты, с которыми новая версия несовместима.
    conflicts: List[str] = field(default_factory=list)
    #: Зависимости, которые не удалось подобрать (нет подходящей версии).
    unresolved: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.required or self.optional or self.conflicts or self.unresolved)


def _dependency_entries(version: ModVersion) -> Iterable[Dict[str, object]]:
    for entry in version.dependencies or []:
        if isinstance(entry, dict):
            yield entry


def resolve_for_version(
    modrinth: Modrinth,
    version: ModVersion,
    *,
    loader: str = "",
    game_version: str = "",
    installed: Sequence[str] = (),
    include_optional: bool = False,
    max_depth: int = MAX_DEPTH,
) -> DependencyPlan:
    """Собирает зависимости версии проекта, которых нет в ``installed``.

    ``installed`` — id проектов Modrinth (не mod id), уже стоящих в
    экземпляре.  Обход рекурсивный, с защитой от циклов и ограничением
    глубины.  Сетевые ошибки не пробрасываются: неподобранная зависимость
    попадает в :attr:`DependencyPlan.unresolved`.
    """
    plan = DependencyPlan()
    installed_ids = {item for item in installed if item}
    seen: Set[str] = set(installed_ids)
    seen_versions: Set[str] = set()

    def walk(current: ModVersion, depth: int, parent: str) -> None:
        if depth > max_depth:
            return
        for entry in _dependency_entries(current):
            kind = str(entry.get("dependency_type") or "required")
            project_id = str(entry.get("project_id") or "")
            version_id = str(entry.get("version_id") or "")
            name = str(entry.get("file_name") or project_id)
            if not project_id and not version_id:
                continue
            if kind == "incompatible":
                if project_id and project_id in installed_ids:
                    plan.conflicts.append(project_id)
                continue
            if kind not in ("required", "optional"):
                continue
            if project_id and project_id in seen:
                continue
            try:
                resolved: Optional[ModVersion] = None
                if version_id:
                    resolved = modrinth.version(version_id)
                if resolved is None and project_id:
                    resolved = modrinth.best_version(
                        project_id,
                        loader=loader,
                        game_version=game_version,
                    )
            except Exception:  # noqa: BLE001 - сеть не должна ломать установку
                resolved = None
            if resolved is None:
                if kind == "required":
                    plan.unresolved.append(project_id or name)
                continue
            key = resolved.project_id or project_id
            if not key or key in seen or resolved.id in seen_versions:
                continue
            seen.add(key)
            seen_versions.add(resolved.id)
            item = ResolvedDependency(
                project_id=key,
                version=resolved,
                parent=parent or version.name,
            )
            if kind == "required":
                plan.required.append(item)
                walk(resolved, depth + 1, resolved.name)
            elif include_optional:
                plan.optional.append(item)

    walk(version, 0, version.name)
    return plan


def missing_local(mods: Sequence[LocalMod]) -> Dict[str, List[str]]:
    """Ищет обязательные зависимости, которых нет среди ``mods``.

    Возвращает ``название мода → список отсутствующих mod id``.
    Учитываются только включённые моды: выключенный мод всё равно не
    загрузится игрой, но и требовать его нет смысла.
    """
    installed = {
        mod.mod_id.strip().lower()
        for mod in mods
        if mod.mod_id and mod.enabled
    }
    result: Dict[str, List[str]] = {}
    for mod in mods:
        if not mod.enabled or not mod.depends:
            continue
        missing = []
        for dep_id in mod.depends:
            key = dep_id.strip().lower()
            if not key or key in BUILTIN_IDS or key in installed:
                continue
            missing.append(dep_id)
        if missing:
            result[mod.title or mod.filename] = sorted(missing)
    return result


def disabled_requirements(mods: Sequence[LocalMod]) -> Dict[str, List[str]]:
    """Зависимости, которые есть в каталоге, но выключены."""
    disabled = {
        mod.mod_id.strip().lower()
        for mod in mods
        if mod.mod_id and not mod.enabled
    }
    result: Dict[str, List[str]] = {}
    for mod in mods:
        if not mod.enabled:
            continue
        needed = [
            dep_id
            for dep_id in mod.depends
            if dep_id.strip().lower() in disabled
        ]
        if needed:
            result[mod.title or mod.filename] = sorted(needed)
    return result
