"""Списки модов: экспорт «чем поделиться» и сравнение экземпляров."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import APP_VERSION
from .manager import DISABLED_SUFFIX

SCHEMA = 1


@dataclass
class ModEntry:
    """Один мод в списке экземпляра."""

    key: str
    name: str
    filename: str
    version: str = ""
    project_id: str = ""
    version_id: str = ""
    mod_id: str = ""
    loader: str = ""
    enabled: bool = True

    @property
    def project_url(self) -> str:
        return f"https://modrinth.com/project/{self.project_id}" if self.project_id else ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "filename": self.filename,
            "version": self.version,
            "project_id": self.project_id,
            "version_id": self.version_id,
            "mod_id": self.mod_id,
            "loader": self.loader,
            "enabled": self.enabled,
        }


@dataclass
class Comparison:
    """Результат сравнения двух экземпляров."""

    left_name: str
    right_name: str
    only_left: List[ModEntry] = field(default_factory=list)
    only_right: List[ModEntry] = field(default_factory=list)
    differing: List[tuple] = field(default_factory=list)  # (left, right)
    same: List[ModEntry] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.only_left and not self.only_right and not self.differing


def _base_name(filename: str) -> str:
    if filename.endswith(DISABLED_SUFFIX):
        return filename[: -len(DISABLED_SUFFIX)]
    return filename


def _entry_key(record: Any, mod: Any, base: str) -> str:
    project_id = str(getattr(record, "project_id", "") or "")
    if project_id:
        return f"project:{project_id}"
    mod_id = str(getattr(mod, "mod_id", "") or "")
    if mod_id:
        return f"mod:{mod_id.lower()}"
    return f"file:{base.lower()}"


def collect(manager: Any, instance: Any) -> List[ModEntry]:
    """Собирает моды экземпляра, дополняя их данными из индекса Modrinth."""
    records: Dict[str, Any] = {}
    try:
        for record in manager.installed_index(instance).items():
            if str(getattr(record, "project_type", "mod")) not in ("mod", ""):
                continue
            records[str(getattr(record, "filename", ""))] = record
    except Exception:  # noqa: BLE001 - индекс не обязателен
        records = {}

    entries: Dict[str, ModEntry] = {}
    for mod in manager.list_mods(instance):
        base = _base_name(mod.filename)
        record = records.get(base) or records.get(mod.filename)
        key = _entry_key(record, mod, base)
        version = str(getattr(record, "version_number", "") or mod.version or "")
        entries[key] = ModEntry(
            key=key,
            name=str(mod.title or getattr(record, "title", "") or base),
            filename=mod.filename,
            version=version,
            project_id=str(getattr(record, "project_id", "") or ""),
            version_id=str(getattr(record, "version_id", "") or ""),
            mod_id=str(mod.mod_id or ""),
            loader=str(mod.loader or ""),
            enabled=bool(mod.enabled),
        )
    return sorted(entries.values(), key=lambda item: item.name.lower())


def _instance_header(instance: Any) -> Dict[str, Any]:
    return {
        "name": str(getattr(instance, "name", "")),
        "display_name": str(getattr(instance, "display_name", "") or getattr(instance, "name", "")),
        "minecraft": str(getattr(instance, "game_version", "") or getattr(instance, "version_id", "")),
        "version_id": str(getattr(instance, "version_id", "")),
        "loader": str(getattr(instance, "loader", "")),
        "loader_version": str(getattr(instance, "loader_version", "")),
        "icon": str(getattr(instance, "icon", "")),
    }


def render_markdown(instance: Any, entries: List[ModEntry]) -> str:
    """Список модов в виде markdown-таблицы."""
    header = _instance_header(instance)
    title = header["display_name"] or header["name"]
    lines = [
        f"# Моды — {title}",
        "",
        f"- Minecraft: {header['minecraft'] or '—'}",
        f"- Загрузчик: {header['loader'] or 'vanilla'} {header['loader_version']}".rstrip(),
        f"- Модов: {len(entries)}",
        f"- Лаунчер: PenguinCraft {APP_VERSION}",
        "",
    ]
    if not entries:
        lines.append("_Модов нет._")
        return "\n".join(lines) + "\n"

    lines += ["| Мод | Версия | Файл | Состояние |", "|---|---|---|---|"]
    for entry in entries:
        name = entry.name.replace("|", "\\|")
        if entry.project_url:
            name = f"[{name}]({entry.project_url})"
        version = (entry.version or "—").replace("|", "\\|")
        filename = f"`{entry.filename}`" if not entry.enabled else entry.filename
        state = "включён" if entry.enabled else "выключен"
        lines.append(f"| {name} | {version} | {filename} | {state} |")
    return "\n".join(lines) + "\n"


def render_json(instance: Any, entries: List[ModEntry]) -> str:
    payload = {
        "schema": SCHEMA,
        "launcher": {"name": "PenguinCraft", "version": APP_VERSION},
        "instance": _instance_header(instance),
        "mods": [entry.to_dict() for entry in entries],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def export_mod_list(
    manager: Any,
    instance: Any,
    target: Path | str,
    *,
    fmt: str = "md",
) -> Path:
    """Сохраняет список модов (``md`` или ``json``)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = collect(manager, instance)
    text = render_json(instance, entries) if fmt == "json" else render_markdown(instance, entries)
    target.write_text(text, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Сравнение
# ---------------------------------------------------------------------------


def compare(manager: Any, left: Any, right: Any) -> Comparison:
    """Сравнивает два экземпляра по модам."""
    left_entries = {entry.key: entry for entry in collect(manager, left)}
    right_entries = {entry.key: entry for entry in collect(manager, right)}
    result = Comparison(
        left_name=str(getattr(left, "display_name", "") or getattr(left, "name", "")),
        right_name=str(getattr(right, "display_name", "") or getattr(right, "name", "")),
    )
    for key, entry in left_entries.items():
        other = right_entries.get(key)
        if other is None:
            result.only_left.append(entry)
        elif (entry.version or entry.filename) != (other.version or other.filename):
            result.differing.append((entry, other))
        else:
            result.same.append(entry)
    for key, entry in right_entries.items():
        if key not in left_entries:
            result.only_right.append(entry)
    result.only_left.sort(key=lambda item: item.name.lower())
    result.only_right.sort(key=lambda item: item.name.lower())
    result.differing.sort(key=lambda pair: pair[0].name.lower())
    return result


def render_comparison(comparison: Comparison) -> str:
    """Текст сравнения для показа в интерфейсе и консоли."""
    left = comparison.left_name
    right = comparison.right_name
    lines = [f"«{left}» ↔ «{right}»", ""]
    if comparison.identical:
        return "\n".join(lines + ["Наборы модов совпадают."])

    lines.append(
        f"Совпадает: {len(comparison.same)} · "
        f"только слева: {len(comparison.only_left)} · "
        f"только справа: {len(comparison.only_right)} · "
        f"разные версии: {len(comparison.differing)}"
    )

    if comparison.only_left:
        lines += ["", f"Только в «{left}»:"]
        lines += [f"  • {item.name} {item.version}".rstrip() for item in comparison.only_left]
    if comparison.only_right:
        lines += ["", f"Только в «{right}»:"]
        lines += [f"  • {item.name} {item.version}".rstrip() for item in comparison.only_right]
    if comparison.differing:
        lines += ["", "Разные версии:"]
        for item_left, item_right in comparison.differing:
            lines.append(
                f"  • {item_left.name}: {item_left.version or item_left.filename} "
                f"↔ {item_right.version or item_right.filename}"
            )
    return "\n".join(lines)
