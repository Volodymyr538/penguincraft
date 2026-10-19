"""Индекс установленных с Modrinth проектов и источник модпака.

Лаунчер запоминает, какой проект и какая версия были скачаны, чтобы уметь
проверять обновления.  Файл лежит рядом с описанием экземпляра
(``installed.json``) и не попадает в каталог игры — Minecraft его не видит.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


@dataclass
class InstalledProject:
    """Запись о скачанном проекте (моде, ресурспаке, шейдере, модпаке)."""

    filename: str = ""
    project_id: str = ""
    version_id: str = ""
    version_number: str = ""
    title: str = ""
    project_type: str = "mod"
    project_slug: str = ""
    installed_at: float = 0.0
    #: Список файлов, поставленных модпаком (только для записи модпака).
    files: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "InstalledProject":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data: Dict[str, Any] = {key: value for key, value in raw.items() if key in known}
        files = data.get("files")
        if isinstance(files, list):
            data["files"] = [str(item) for item in files]
        else:
            data.pop("files", None)
        try:
            return cls(**data)
        except TypeError:
            return cls()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InstalledIndex:
    """Набор записей для одного экземпляра."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.files: Dict[str, InstalledProject] = {}
        self.pack: Optional[InstalledProject] = None

    # ------------------------------------------------------------------
    def load(self) -> "InstalledIndex":
        self.files = {}
        self.pack = None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self
        if not isinstance(raw, Mapping):
            return self
        files = raw.get("files")
        if isinstance(files, Mapping):
            for name, item in files.items():
                if not isinstance(item, Mapping):
                    continue
                record = InstalledProject.from_dict(item)
                record.filename = record.filename or str(name)
                self.files[str(name)] = record
        pack = raw.get("modpack")
        if isinstance(pack, Mapping):
            self.pack = InstalledProject.from_dict(pack)
        return self

    def save(self) -> None:
        payload: Dict[str, Any] = {
            "version": 1,
            "files": {name: item.to_dict() for name, item in self.files.items()},
        }
        if self.pack is not None:
            payload["modpack"] = self.pack.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    def get(self, filename: str) -> Optional[InstalledProject]:
        return self.files.get(filename)

    def items(self) -> List[InstalledProject]:
        return list(self.files.values())

    def record(self, filename: str, **meta: Any) -> InstalledProject:
        """Запоминает скачанный файл."""
        record = InstalledProject(filename=filename, installed_at=time.time(), **meta)
        self.files[filename] = record
        return record

    def forget(self, filenames: Iterable[str]) -> int:
        removed = 0
        for name in list(filenames):
            if self.files.pop(name, None) is not None:
                removed += 1
        return removed

    def prune(self, existing: Iterable[str]) -> int:
        """Убирает записи о файлах, которых больше нет в каталоге."""
        keep = set(existing)
        removed = 0
        for name in list(self.files):
            if name not in keep:
                self.files.pop(name, None)
                removed += 1
        return removed

    def set_pack(self, **meta: Any) -> InstalledProject:
        record = InstalledProject(installed_at=time.time(), **meta)
        self.pack = record
        return record
