"""Экспорт диагностики: один zip, который не стыдно приложить к багу.

Внутрь попадают версия лаунчера, система, найденная Java, список
экземпляров и последние журналы.  Аккаунты и токены **не** включаются,
а пароль из строки прокси вырезается.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import APP_VERSION
from .paths import Paths
from .settings import Settings
from .storage import measure
from .task import Reporter
from .util import human_size

#: Сколько последних журналов брать.
MAX_LOGS = 5
#: Сколько байт от каждого журнала (хвост).
MAX_LOG_BYTES = 1024 * 1024
#: Сколько отчётов о падении на экземпляр.
MAX_CRASHES = 2
MAX_CRASH_BYTES = 256 * 1024

_PROXY_CREDENTIALS = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")


def _redact_proxy(value: str) -> str:
    return _PROXY_CREDENTIALS.sub(r"\1***\3", value or "")


def _settings_snapshot(settings: Settings) -> Dict[str, Any]:
    data = {
        "theme": settings.theme,
        "jvm_preset": settings.jvm_preset,
        "memory_mode": settings.memory_mode,
        "memory_mb": settings.memory_mb,
        "auto_download_java": settings.auto_download_java,
        "java_path": settings.java_path,
        "resolution": [settings.resolution_width, settings.resolution_height],
        "fullscreen": settings.fullscreen,
        "force_lwjgl_software": settings.force_lwjgl_software,
        "parallel_downloads": settings.parallel_downloads,
        "download_limit_kbps": settings.download_limit_kbps,
        "install_dependencies": settings.install_dependencies,
        "auto_update_mods": settings.auto_update_mods,
        "check_launcher_updates": settings.check_launcher_updates,
        "ui_scale": settings.ui_scale,
        "proxy": _redact_proxy(settings.proxy),
    }
    return data


def build_report(manager: Any) -> Dict[str, Any]:
    """Собирает сведения о системе и лаунчере (без секретов)."""
    paths: Paths = manager.paths
    settings: Settings = manager.settings

    total_size, total_files = measure(paths.root)
    try:
        instances = manager.instances.list()
    except Exception:  # noqa: BLE001 - отчёт не должен падать
        instances = []

    java_items: List[Dict[str, Any]] = []
    try:
        for runtime in manager.java_runtimes():
            java_items.append(
                {
                    "title": runtime.title,
                    "path": str(runtime.path),
                    "version": runtime.version,
                    "major": runtime.major,
                    "arch": runtime.arch,
                    "vendor": runtime.vendor,
                    "source": runtime.source,
                    "managed": runtime.managed,
                }
            )
    except Exception:  # noqa: BLE001
        pass

    instance_items: List[Dict[str, Any]] = []
    for instance in instances:
        try:
            mods = len(list(manager.instances.mods_dir(instance.name).glob("*.jar")))
            size = manager.instances.disk_usage(instance.name)
        except Exception:  # noqa: BLE001
            mods = 0
            size = 0
        instance_items.append(
            {
                "name": instance.name,
                "display_name": instance.display_name,
                "version_id": instance.version_id,
                "game_version": instance.game_version,
                "loader": instance.loader,
                "loader_version": instance.loader_version,
                "mods": mods,
                "size": size,
                "last_played": instance.last_played,
            }
        )

    accounts = list(manager.accounts)
    account_types: Dict[str, int] = {}
    for account in accounts:
        account_types[account.type_title] = account_types.get(account.type_title, 0) + 1

    try:
        installed_versions = manager.local_version_list()
        version_names = [
            str(getattr(item, "version_id", "") or getattr(item, "id", ""))
            for item in installed_versions
        ]
        version_names = [name for name in version_names if name]
    except Exception:  # noqa: BLE001
        version_names = []

    free, disk_total = 0, 0
    try:
        from .storage import free_space

        free, disk_total = free_space(paths.root)
    except Exception:  # noqa: BLE001
        pass

    return {
        "launcher": {
            "name": "PenguinCraft",
            "version": APP_VERSION,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "system": {
            "platform": sys.platform,
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count() or 0,
            "encoding": sys.getfilesystemencoding(),
        },
        "paths": {
            "root": str(paths.root),
            "settings": str(paths.settings),
            "launcher_size": total_size,
            "launcher_files": total_files,
        },
        "disk": {
            "free": free,
            "total": disk_total,
        },
        "java": java_items,
        "accounts": {"count": len(accounts), "types": account_types},
        "instances": instance_items,
        "versions": version_names,
        "settings": _settings_snapshot(settings),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_text(report: Dict[str, Any]) -> str:
    """Человекочитаемый отчёт (русский)."""
    launcher = report.get("launcher", {})
    system = report.get("system", {})
    python = report.get("python", {})
    paths = report.get("paths", {})
    disk = report.get("disk", {})
    lines: List[str] = [
        f"PenguinCraft {launcher.get('version', '?')} — отчёт диагностики",
        f"Создан: {report.get('generated_at', '')}",
        "",
        "== Система ==",
        f"  Платформа: {system.get('platform')} {system.get('release')}",
        f"  Ядро/сборка: {system.get('version')}",
        f"  Архитектура: {system.get('machine')}",
        f"  Ядер CPU: {system.get('cpu_count')}",
        f"  Кодировка ФС: {system.get('encoding')}",
        f"  Python: {python.get('version')} ({python.get('implementation')})",
        f"  Собран в .exe: {'да' if launcher.get('frozen') else 'нет'}",
        "",
        "== Каталог данных ==",
        f"  {paths.get('root')}",
        f"  Занято лаунчером: {human_size(paths.get('launcher_size', 0))} "
        f"({paths.get('launcher_files', 0)} файлов)",
        f"  Свободно на диске: {human_size(disk.get('free', 0))} "
        f"из {human_size(disk.get('total', 0))}",
        "",
        "== Java ==",
    ]
    java = report.get("java") or []
    if java:
        for item in java:
            lines.append(f"  • {item.get('title')}: {item.get('path')}")
    else:
        lines.append("  не найдена")

    accounts = report.get("accounts") or {}
    lines += [
        "",
        "== Аккаунты ==",
        f"  Всего: {accounts.get('count', 0)}",
    ]
    for kind, count in sorted((accounts.get("types") or {}).items()):
        lines.append(f"  • {kind}: {count}")

    lines += ["", "== Экземпляры =="]
    instances = report.get("instances") or []
    if instances:
        for item in instances:
            lines.append(
                f"  • {item.get('display_name')} "
                f"[{item.get('loader')} {item.get('loader_version') or ''}] "
                f"{item.get('version_id') or item.get('game_version') or '—'}, "
                f"модов: {item.get('mods')}, {human_size(item.get('size', 0))}"
            )
    else:
        lines.append("  нет")

    lines += ["", "== Установленные версии =="]
    versions = report.get("versions") or []
    lines.append("  " + (", ".join(versions) if versions else "нет"))

    lines += ["", "== Настройки =="]
    for key, value in sorted((report.get("settings") or {}).items()):
        lines.append(f"  {key}: {value}")

    lines += ["", "Файлы журналов и отчётов о падениях — в этом же архиве."]
    return "\n".join(lines) + "\n"


def _tail(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            return handle.read()
    except OSError:
        return b""


def _recent_logs(paths: Paths) -> List[Path]:
    if not paths.logs.is_dir():
        return []
    entries = [item for item in paths.logs.rglob("*") if item.is_file()]
    entries.sort(key=lambda item: _mtime(item), reverse=True)
    return entries[:MAX_LOGS]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _crash_reports(manager: Any) -> List[Path]:
    result: List[Path] = []
    try:
        instances = manager.instances.list()
    except Exception:  # noqa: BLE001
        return result
    for instance in instances:
        directory = manager.instances.game_dir(instance.name) / "crash-reports"
        if not directory.is_dir():
            continue
        entries = [item for item in directory.glob("*.txt") if item.is_file()]
        entries.sort(key=_mtime, reverse=True)
        for item in entries[:MAX_CRASHES]:
            result.append(item)
    return result


def export_diagnostics(
    manager: Any,
    target: Path | str,
    *,
    reporter: Optional[Reporter] = None,
) -> Path:
    """Собирает zip с диагностикой.  Возвращает путь к архиву."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if reporter:
        reporter.status("Сбор сведений о системе…")
        reporter.check_cancel()
    report = build_report(manager)
    text = render_text(report)

    paths: Paths = manager.paths
    tmp = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.txt", text)
            archive.writestr(
                "report.json",
                json.dumps(report, indent=2, ensure_ascii=False),
            )

            if reporter:
                reporter.status("Добавление журналов…")
            for log in _recent_logs(paths):
                if reporter:
                    reporter.check_cancel()
                try:
                    name = log.relative_to(paths.logs).as_posix()
                except ValueError:
                    name = log.name
                archive.writestr(
                    f"logs/{name}",
                    _tail(log, MAX_LOG_BYTES),
                )

            crashes = _crash_reports(manager)
            for number, crash in enumerate(crashes, start=1):
                if reporter:
                    reporter.check_cancel()
                parents = crash.parents
                instance = parents[2].name if len(parents) > 2 else "instance"
                archive.writestr(
                    f"crash-reports/{instance}-{number}-{crash.name}",
                    _tail(crash, MAX_CRASH_BYTES),
                )
        tmp.replace(target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    if reporter:
        reporter.log(f"Диагностика сохранена: {target}")
    return target


def default_name() -> str:
    return f"penguincraft-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
