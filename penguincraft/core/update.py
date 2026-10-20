"""Обновление самого лаунчера через GitHub Releases."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .. import APP_VERSION
from .net import Http
from .task import Reporter

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
    asset_urls: Dict[str, str] = field(default_factory=dict)
    asset_sizes: Dict[str, int] = field(default_factory=dict)

    @property
    def is_newer(self) -> bool:
        return version_is_newer(self.version, APP_VERSION)


@dataclass
class UpdateAsset:
    """Файл обновления, подходящий текущей системе."""

    name: str
    url: str
    size: int = 0
    kind: str = ""


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
    assets: List[str] = []
    urls: Dict[str, str] = {}
    sizes: Dict[str, int] = {}
    for item in data.get("assets") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        assets.append(name)
        url = str(item.get("browser_download_url") or "")
        if url:
            urls[name] = url
        try:
            sizes[name] = int(item.get("size") or 0)
        except (TypeError, ValueError):
            sizes[name] = 0
    return ReleaseInfo(
        version=version,
        tag=tag,
        url=str(data.get("html_url") or RELEASES_PAGE),
        notes=str(data.get("body") or ""),
        published=str(data.get("published_at") or ""),
        assets=assets,
        asset_urls=urls,
        asset_sizes=sizes,
    )


def check_latest(
    http: Http,
    *,
    url: str = LATEST_URL,
    raise_errors: bool = False,
) -> Optional[ReleaseInfo]:
    """Возвращает последний релиз или ``None``, если проверить не удалось."""
    try:
        data = http.get_json(url, headers={"Accept": "application/vnd.github+json"})
    except Exception:  # noqa: BLE001 - офлайн не должен мешать работе
        if raise_errors:
            raise
        return None
    return parse_release(data) if isinstance(data, Mapping) else None


# ---------------------------------------------------------------------------
# Выбор файла под текущую систему
# ---------------------------------------------------------------------------


def classify_asset(name: str) -> str:
    """Определяет назначение файла релиза по имени."""
    lowered = name.lower()
    if lowered.endswith("-setup.exe"):
        return "windows-setup"
    if lowered.endswith("-portable.exe"):
        return "windows-portable"
    if lowered.endswith("-installer.sh"):
        return "linux-installer"
    if lowered.endswith(".deb"):
        return "linux-deb"
    return "other"


def pick_asset(
    release: ReleaseInfo,
    *,
    platform: str = "",
    portable: bool = False,
) -> Optional[UpdateAsset]:
    """Выбирает подходящий файл обновления.

    Windows — ``setup.exe`` (обычная установка) или ``portable.exe``,
    Linux/ChromeOS — ``installer.sh`` (не требует root).  ``.deb`` не
    выбираем автоматически: для него нужен sudo.
    """
    platform = (platform or sys.platform).lower()
    preferred: List[str]
    if platform.startswith("win"):
        preferred = (
            ["windows-portable", "windows-setup"]
            if portable
            else ["windows-setup", "windows-portable"]
        )
    elif platform.startswith("linux"):
        preferred = ["linux-installer", "linux-deb"]
    else:
        return None

    for kind in preferred:
        for name in release.assets:
            if classify_asset(name) != kind:
                continue
            url = release.asset_urls.get(name, "")
            if not url:
                continue
            return UpdateAsset(
                name=name,
                url=url,
                size=release.asset_sizes.get(name, 0),
                kind=kind,
            )
    return None


def download_update(
    http: Http,
    asset: UpdateAsset,
    directory: Path | str,
    *,
    reporter: Optional[Reporter] = None,
) -> Path:
    """Скачивает файл обновления в *directory* (с докачкой)."""
    target = Path(directory) / asset.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if reporter:
        reporter.status(f"Загрузка обновления {asset.name}…")
    return http.download(
        asset.url,
        target,
        size=asset.size or None,
        reporter=reporter,
    )


def asset_kind_title(kind: str) -> str:
    return {
        "windows-setup": "установщик Windows",
        "windows-portable": "портативная версия для Windows",
        "linux-installer": "установщик для Linux",
        "linux-deb": "пакет .deb",
    }.get(kind, "файл обновления")
