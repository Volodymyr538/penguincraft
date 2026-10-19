"""Проверка обновлений самого лаунчера через GitHub Releases."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Tuple

from .. import APP_VERSION
from .net import Http

#: Репозиторий проекта.
REPO = "Volodymyr538/penguincraft"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


@dataclass
class ReleaseInfo:
    """Сведения о последнем релизе."""

    version: str
    tag: str
    url: str
    notes: str = ""
    published: str = ""
    assets: List[str] = field(default_factory=list)

    @property
    def is_newer(self) -> bool:
        return version_is_newer(self.version, APP_VERSION)


def parse_version(text: str) -> Tuple[int, ...]:
    """«v1.2.3» → ``(1, 2, 3)``.  Нечисловые хвосты отбрасываются."""
    cleaned = str(text or "").strip().lower().lstrip("v")
    parts = re.split(r"[.\-+]", cleaned)
    numbers: List[int] = []
    for part in parts:
        match = re.match(r"^(\d+)", part)
        if not match:
            break
        numbers.append(int(match.group(1)))
    return tuple(numbers) or (0,)


def version_is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    return parse_version(candidate) > parse_version(current)


def parse_release(data: Mapping[str, Any]) -> Optional[ReleaseInfo]:
    if not isinstance(data, Mapping):
        return None
    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    if not tag:
        return None
    version = tag.lstrip("vV")
    assets = []
    for item in data.get("assets") or []:
        if isinstance(item, Mapping) and item.get("name"):
            assets.append(str(item["name"]))
    return ReleaseInfo(
        version=version,
        tag=tag,
        url=str(data.get("html_url") or RELEASES_PAGE),
        notes=str(data.get("body") or ""),
        published=str(data.get("published_at") or ""),
        assets=assets,
    )


def check_latest(http: Http, *, url: str = LATEST_URL) -> Optional[ReleaseInfo]:
    """Возвращает последний релиз или ``None``, если проверить не удалось."""
    try:
        data = http.get_json(url, headers={"Accept": "application/vnd.github+json"})
    except Exception:  # noqa: BLE001 - офлайн не должен мешать работе
        return None
    return parse_release(data) if isinstance(data, Mapping) else None
