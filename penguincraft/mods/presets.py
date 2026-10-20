"""Пресет оптимизации: набор проверенных модов для слабых машин.

Ставится одним действием; всё, для чего нет версии под конкретную
версию игры/загрузчик, просто пропускается с записью в журнал.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class PresetProject:
    """Проект Modrinth, входящий в пресет."""

    slug: str
    title: str
    loaders: Tuple[str, ...]
    note: str = ""


#: Порядок важен: Sodium первым — он даёт наибольший прирост FPS.
OPTIMIZATION_PROJECTS: Tuple[PresetProject, ...] = (
    PresetProject(
        "sodium", "Sodium", ("fabric", "quilt", "neoforge"),
        "переписанный рендер: ощутимый прирост FPS",
    ),
    PresetProject(
        "lithium", "Lithium", ("fabric", "quilt", "neoforge"),
        "оптимизация игровой логики и ИИ",
    ),
    PresetProject(
        "ferrite-core", "FerriteCore", ("fabric", "quilt", "forge", "neoforge"),
        "меньше памяти под модели и текстуры",
    ),
    PresetProject(
        "krypton", "Krypton", ("fabric", "quilt"),
        "оптимизация сетевого стека",
    ),
    PresetProject(
        "entityculling", "EntityCulling", ("fabric", "quilt", "forge", "neoforge"),
        "не рисует то, что не видно",
    ),
    PresetProject(
        "immediatelyfast", "ImmediatelyFast", ("fabric", "quilt", "neoforge"),
        "ускоряет интерфейс и отрисовку",
    ),
    PresetProject(
        "modernfix", "ModernFix", ("forge", "neoforge"),
        "быстрый старт и меньше памяти (Forge/NeoForge)",
    ),
)

#: Загрузчики, которые считаются совместимыми друг с другом.
LOADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "quilt": ("quilt", "fabric"),
    "neoforge": ("neoforge", "forge"),
}


def projects_for(loader: str) -> List[PresetProject]:
    """Проекты пресета, подходящие загрузчику экземпляра."""
    name = (loader or "vanilla").lower()
    if name == "vanilla":
        return []
    allowed = LOADER_ALIASES.get(name, (name,))
    return [item for item in OPTIMIZATION_PROJECTS if any(alias in item.loaders for alias in allowed)]


def titles_for(loader: str) -> List[str]:
    return [item.title for item in projects_for(loader)]
