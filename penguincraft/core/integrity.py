"""Проверка целостности файлов игры и восстановление повреждённых.

Проверка нужна после сбоя питания, неудачного обновления драйверов или
просто «игра не запускается, а почему — непонятно».  По умолчанию
сравниваются размеры (быстро); в глубоком режиме дополнительно считаются
sha1 библиотек и ресурсов (дольше, зато ловит битые файлы того же размера).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .assets import RESOURCES_URL, load_index
from .installer import InstallPlan, client_jar_path, install_version
from .libraries import build_plan
from .mojang import VersionRepository
from .net import Http
from .paths import Paths
from .rules import default_platform
from .settings import Settings
from .task import Reporter
from .util import hash_file


@dataclass
class IntegrityIssue:
    """Один проблемный файл."""

    kind: str                 # client | library | asset | index
    path: Path
    reason: str               # missing | size | hash
    url: str = ""
    sha1: str = ""
    size: int = 0

    @property
    def title(self) -> str:
        return {
            "client": "Клиент игры",
            "library": "Библиотека",
            "asset": "Ресурс",
            "index": "Индекс ресурсов",
        }.get(self.kind, self.kind)


@dataclass
class IntegrityReport:
    """Итог проверки."""

    version_id: str = ""
    checked: int = 0
    issues: List[IntegrityIssue] = field(default_factory=list)
    repaired: int = 0
    failed: List[str] = field(default_factory=list)
    deep: bool = False

    @property
    def ok(self) -> bool:
        return not self.issues

    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for issue in self.issues:
            result[issue.kind] = result.get(issue.kind, 0) + 1
        return result

    def summary(self) -> str:
        if self.ok:
            text = f"Проверено файлов: {self.checked}. Проблем не найдено."
            if self.repaired:
                text += f" Восстановлено: {self.repaired}."
            return text
        parts = [f"Проверено файлов: {self.checked}."]
        for kind, count in sorted(self.counts().items()):
            parts.append(f"{kind}: {count}")
        if self.repaired:
            parts.append(f"восстановлено: {self.repaired}")
        if self.failed:
            parts.append(f"не удалось восстановить: {len(self.failed)}")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Проверка
# ---------------------------------------------------------------------------


def _expected_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def check_version(
    paths: Paths,
    version: Mapping[str, Any],
    version_id: str,
    *,
    deep: bool = False,
    reporter: Optional[Reporter] = None,
    platform: Optional[Mapping[str, Any]] = None,
    check_assets: bool = True,
) -> IntegrityReport:
    """Проверяет файлы уже установленной версии (без сети)."""
    report = IntegrityReport(version_id=version_id, deep=deep)
    plat = platform or default_platform()

    def progress(current: int, total: int, detail: str = "") -> None:
        if reporter:
            reporter.progress(current, total, detail)

    # -- клиент ---------------------------------------------------------
    downloads = version.get("downloads") or {}
    client = downloads.get("client") if isinstance(downloads, Mapping) else None
    jar = client_jar_path(paths, version_id, version)
    if isinstance(client, Mapping) and client.get("url"):
        report.checked += 1
        sha1 = str(client.get("sha1") or "")
        size = int(client.get("size") or 0)
        if not jar.is_file():
            report.issues.append(
                IntegrityIssue("client", jar, "missing", str(client.get("url") or ""), sha1, size)
            )
        elif size and jar.stat().st_size != size:
            report.issues.append(
                IntegrityIssue("client", jar, "size", str(client.get("url") or ""), sha1, size)
            )
        elif deep and sha1 and hash_file(jar, "sha1") != sha1:
            report.issues.append(
                IntegrityIssue("client", jar, "hash", str(client.get("url") or ""), sha1, size)
            )
    elif not jar.is_file():
        report.issues.append(IntegrityIssue("client", jar, "missing"))

    # -- библиотеки -----------------------------------------------------
    plan = build_plan(version.get("libraries") or [], plat)
    files = plan.all_files()
    total = len(files) + 1
    for number, item in enumerate(files, start=1):
        target = paths.libraries / item.relative
        report.checked += 1
        if not target.is_file():
            report.issues.append(
                IntegrityIssue("library", target, "missing", item.url, item.sha1, item.size)
            )
        elif item.size and target.stat().st_size != item.size:
            report.issues.append(
                IntegrityIssue("library", target, "size", item.url, item.sha1, item.size)
            )
        elif deep and item.sha1 and hash_file(target, "sha1") != item.sha1.lower():
            report.issues.append(
                IntegrityIssue("library", target, "hash", item.url, item.sha1, item.size)
            )
        progress(number, total, "Проверка библиотек")

    # -- ресурсы --------------------------------------------------------
    if check_assets:
        asset_index = version.get("assetIndex") or {}
        index_id = str(
            (asset_index.get("id") if isinstance(asset_index, Mapping) else "")
            or version.get("assets")
            or ""
        )
        index = load_index(paths, index_id) if index_id else None
        if index_id and index is None:
            report.issues.append(
                IntegrityIssue(
                    "index",
                    paths.asset_indexes / f"{index_id}.json",
                    "missing",
                    str(asset_index.get("url") or "") if isinstance(asset_index, Mapping) else "",
                    str(asset_index.get("sha1") or "") if isinstance(asset_index, Mapping) else "",
                    int(asset_index.get("size") or 0) if isinstance(asset_index, Mapping) else 0,
                )
            )
        objects = index.get("objects") if isinstance(index, Mapping) else None
        if isinstance(objects, Mapping):
            total_objects = len(objects)
            for number, meta in enumerate(objects.values(), start=1):
                if not isinstance(meta, Mapping):
                    continue
                object_hash = str(meta.get("hash") or "")
                if not object_hash:
                    continue
                target = paths.asset_objects / object_hash[:2] / object_hash
                report.checked += 1
                expected_size = int(meta.get("size") or 0)
                if not target.is_file():
                    report.issues.append(
                        IntegrityIssue(
                            "asset",
                            target,
                            "missing",
                            f"{RESOURCES_URL}/{object_hash[:2]}/{object_hash}",
                            object_hash,
                            expected_size,
                        )
                    )
                elif expected_size and target.stat().st_size != expected_size:
                    report.issues.append(
                        IntegrityIssue(
                            "asset",
                            target,
                            "size",
                            f"{RESOURCES_URL}/{object_hash[:2]}/{object_hash}",
                            object_hash,
                            expected_size,
                        )
                    )
                elif deep and hash_file(target, "sha1") != object_hash:
                    report.issues.append(
                        IntegrityIssue(
                            "asset",
                            target,
                            "hash",
                            f"{RESOURCES_URL}/{object_hash[:2]}/{object_hash}",
                            object_hash,
                            expected_size,
                        )
                    )
                if number % 200 == 0:
                    progress(number, total_objects, "Проверка ресурсов")
    if reporter:
        reporter.status("Проверка завершена")
    return report


# ---------------------------------------------------------------------------
# Восстановление
# ---------------------------------------------------------------------------


def repair_version(
    paths: Paths,
    settings: Settings,
    version_id: str,
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    game_dir: Optional[Path] = None,
    repository: Optional[VersionRepository] = None,
    deep: bool = False,
    java_path: str = "",
) -> IntegrityReport:
    """Проверяет версию, удаляет битые файлы и докачивает их."""
    repo = repository or VersionRepository(paths, http)
    version = repo.resolve(version_id, reporter=reporter)
    report = check_version(paths, version, version_id, deep=deep, reporter=reporter)
    if report.ok:
        return report

    removed = 0
    for issue in report.issues:
        try:
            if issue.path.is_file():
                issue.path.unlink()
                removed += 1
        except OSError:
            continue
    if reporter:
        reporter.log(f"Удалено повреждённых файлов: {removed}")

    plan: Optional[InstallPlan] = None
    try:
        plan = install_version(
            paths,
            settings,
            version_id,
            http,
            reporter=reporter,
            game_dir=game_dir,
            repository=repo,
            java_path=java_path,
        )
    except Exception as error:  # noqa: BLE001 - показываем в отчёте
        report.failed.append(str(error))

    # Перепроверяем только то, что было сломано.
    after = check_version(paths, plan.version if plan else version, version_id, deep=deep)
    fixed = len(report.issues) - len(after.issues)
    report.repaired = max(0, fixed)
    report.issues = after.issues
    return report
