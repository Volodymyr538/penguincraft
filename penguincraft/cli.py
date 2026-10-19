"""Консольный интерфейс PenguinCraft.

Полезен на хромбуках для диагностики, установки без графики и для
настройки по SSH.  Графический интерфейс запускается командой ``gui``
(или просто ``penguincraft`` без аргументов).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import APP_NAME, APP_VERSION
from .core.task import Cancelled, Event, Reporter
from .core.util import human_size, is_chromebook
from .manager import LauncherError, LauncherManager


# ---------------------------------------------------------------------------
# Приёмник событий для консоли
# ---------------------------------------------------------------------------


class ConsoleReporter:
    """Печатает статус в одну строку с прогресс-баром.

    Если вывод направлен не в терминал (лог, перенаправление), полоса не
    рисуется вовсе — вместо неё печатаются короткие строки статуса.
    Обновления прогресса дополнительно троттлятся, чтобы не засорять лог.
    """

    def __init__(self, verbose: bool = False, *, stream=None, throttle: float = 0.1) -> None:
        self.verbose = verbose
        self.stream = stream if stream is not None else sys.stdout
        self.throttle = throttle
        self._last_width = 0
        self._last_draw = 0.0
        self._last_percent = -1.0
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())

    def __call__(self, event: Event) -> None:
        if event.kind == "status":
            self._status(event.text)
        elif event.kind == "progress":
            self._progress(event)
        elif event.kind == "log":
            if self.verbose:
                self._clear()
                self._print("  · " + event.text)
        elif event.kind == "error":
            self._clear()
            print(f"  ! {event.text}", file=sys.stderr)
        elif event.kind == "done":
            self._clear()
            self._print(f"  ✓ {event.text}")

    # -- низкий уровень -----------------------------------------------------
    def _print(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def _clear(self) -> None:
        if self._last_width and self._tty:
            self.stream.write("\r" + " " * self._last_width + "\r")
            self.stream.flush()
        self._last_width = 0

    def _status(self, text: str) -> None:
        self._clear()
        if not text:
            return
        if self._tty:
            self._print(f"  → {text}")
        else:
            # в логе статусы не должны дублироваться подряд
            if getattr(self, "_last_status", None) != text:
                self._print(f"  → {text}")
        self._last_status = text

    def _progress(self, event: Event) -> None:
        total = event.total or 0
        if total <= 0:
            return
        fraction = max(0.0, min(1.0, event.current / total))
        if not self._tty:
            # Без терминала прогресс не показываем — печатаем только
            # завершение (100%), чтобы лог оставался читаемым.
            if fraction >= 1.0 and self._last_percent < 1.0:
                self._print(f"  · {event.detail or 'Готово'}: 100%")
            self._last_percent = fraction
            return

        now = time.monotonic()
        if fraction < 1.0 and (now - self._last_draw) < self.throttle:
            return
        self._last_draw = now
        self._last_percent = fraction
        width = 28
        filled = int(width * fraction)
        bar = "█" * filled + "░" * (width - filled)
        line = f"  [{bar}] {fraction * 100:5.1f}%  {event.detail or ''}"
        self._last_width = len(line)
        self.stream.write("\r" + line)
        self.stream.flush()


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------


def cmd_info(manager: LauncherManager, args: argparse.Namespace) -> int:
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Каталог данных: {manager.paths.root}")
    print(f"Хромбук: {'да' if is_chromebook() else 'нет'}")
    print()
    print("Java:")
    runtimes = manager.java_runtimes()
    if not runtimes:
        print("  не найдена (лаунчер скачает автоматически)")
    for runtime in runtimes:
        marker = "✓" if runtime.major else "?"
        print(f"  {marker} {runtime.title}: {runtime.path}")
    print()
    print("Настройки:")
    print(f"  профиль JVM: {manager.settings.jvm_preset}")
    print(f"  память: {manager.settings.effective_memory_mb} МБ")
    print(f"  разрешение: {manager.settings.resolution_width}×{manager.settings.resolution_height}")
    print()
    instances = manager.instances.list()
    print(f"Экземпляры ({len(instances)}):")
    for instance in instances:
        mods = len(list(manager.instances.mods_dir(instance.name).glob("*.jar")))
        size = human_size(manager.instances.disk_usage(instance.name))
        print(
            f"  {instance.icon} {instance.display_name} — {instance.version_id or 'версия не выбрана'} "
            f"[{instance.loader}] модов: {mods}, {size}"
        )
    return 0


def cmd_versions(manager: LauncherManager, args: argparse.Namespace) -> int:
    versions = manager.game_versions(
        snapshots=args.snapshots, old=args.old, force=args.refresh
    )
    for info in versions[: args.limit]:
        print(f"{info.id:<22} {info.type_title:<9} {info.release_date}")
    if len(versions) > args.limit:
        print(f"... и ещё {len(versions) - args.limit}")
    return 0


def cmd_install(manager: LauncherManager, args: argparse.Namespace) -> int:
    reporter = Reporter(ConsoleReporter(args.verbose))
    try:
        version_id = manager.install_version_only(
            args.version, loader=args.loader, loader_version=args.loader_version or "",
            reporter=reporter,
        )
        print(f"Установлена версия: {version_id}")
        instance = manager.instances.get(args.instance) if args.instance else None
        if instance is None:
            existing = [item for item in manager.instances.list() if item.version_id == version_id]
            if existing:
                instance = existing[0]
        if instance is not None:
            print(f"Дозагрузка файлов для экземпляра «{instance.display_name}»…")
            manager.install_instance(instance, reporter=reporter)
            print("Готово. Можно запускать.")
        return 0
    except (LauncherError, Cancelled) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


def cmd_instances(manager: LauncherManager, args: argparse.Namespace) -> int:
    for instance in manager.instances.list():
        mods = len(list(manager.instances.mods_dir(instance.name).glob("*.jar")))
        print(
            f"{instance.name:<24} {instance.display_name:<28} "
            f"{instance.version_id or '-':<40} модов: {mods}"
        )
    return 0


def cmd_create(manager: LauncherManager, args: argparse.Namespace) -> int:
    try:
        instance = manager.instances.create(
            args.name,
            version_id="",
            game_version=args.version,
            loader=args.loader,
            loader_version=args.loader_version or "",
            icon=args.icon,
        )
        # сразу ставим версию
        reporter = Reporter(ConsoleReporter(args.verbose))
        version_id = manager.resolve_version_id(instance, reporter)
        print(f"Создан экземпляр «{instance.display_name}» ({version_id})")
        if args.install:
            manager.install_instance(instance, reporter=reporter)
            print("Установка завершена.")
        return 0
    except (LauncherError, Exception) as error:  # noqa: BLE001
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


def cmd_launch(manager: LauncherManager, args: argparse.Namespace) -> int:
    instance = manager.instances.get(args.instance)
    if instance is None:
        print(f"Экземпляр «{args.instance}» не найден", file=sys.stderr)
        return 1
    account = manager.accounts.active
    if args.offline_name:
        account = manager.add_offline_account(args.offline_name)
    reporter = Reporter(ConsoleReporter(args.verbose))
    lines: List[str] = []

    def on_line(line: str) -> None:
        print(line, flush=True)

    def on_exit(code: int) -> None:
        print(f"\nИгра завершилась с кодом {code}")

    try:
        process = manager.launch(
            instance, account, reporter=reporter, on_line=on_line, on_exit=on_exit
        )
        print(f"Игра запущена (PID {process.pid}). Ctrl+C — остановить.")
        code = process.wait()
        report = process.crash_report()
        if report and code != 0:
            print(f"Отчёт о падении: {report}")
        return code
    except KeyboardInterrupt:
        manager.stop_game()
        return 130
    except LauncherError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


def cmd_accounts(manager: LauncherManager, args: argparse.Namespace) -> int:
    active = manager.accounts.active
    if len(manager.accounts) == 0:
        print("Аккаунтов нет. Добавьте: penguincraft account-add-offline <ник>")
        return 0
    for account in manager.accounts:
        marker = "*" if active and account.id == active.id else " "
        state = "истёк" if account.expired else "активен"
        print(f"{marker} [{account.type_title:<9}] {account.display:<18} {account.uuid[:8]}… ({state})")
    return 0


def cmd_account_add_offline(manager: LauncherManager, args: argparse.Namespace) -> int:
    try:
        account = manager.add_offline_account(args.name)
    except Exception as error:  # noqa: BLE001
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Добавлен офлайн-аккаунт: {account.name} ({account.uuid})")
    return 0


def cmd_account_ms_login(manager: LauncherManager, args: argparse.Namespace) -> int:
    reporter = Reporter(ConsoleReporter(args.verbose))
    prompt_shown = {"done": False}

    def on_prompt(prompt) -> None:
        prompt_shown["done"] = True
        print()
        print("=" * 62)
        print("  ВХОД В MICROSOFT")
        print(f"  1. Откройте: {prompt.verification_uri}")
        print(f"  2. Введите код: {prompt.user_code}")
        print("=" * 62)
        print()

    def on_status(text: str) -> None:
        if args.verbose:
            print(f"  → {text}")

    try:
        account = manager.start_microsoft_login(
            reporter=reporter, on_prompt=on_prompt, on_status=on_status
        )
    except LauncherError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Успешный вход: {account.name} ({account.uuid})")
    return 0


def cmd_account_elyby(manager: LauncherManager, args: argparse.Namespace) -> int:
    import getpass

    password = args.password or getpass.getpass("Пароль Ely.by: ")
    try:
        account = manager.add_elyby_account(args.username, password)
    except LauncherError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Успешный вход: {account.name} ({account.uuid})")
    return 0


def cmd_mod_search(manager: LauncherManager, args: argparse.Namespace) -> int:
    from .mods.modrinth import format_downloads

    try:
        result = manager.modrinth.search(
            args.query,
            project_type=args.type,
            loaders=[args.loader] if args.loader else [],
            game_versions=[args.version] if args.version else [],
            limit=args.limit,
        )
    except Exception as error:  # noqa: BLE001
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Найдено {result.total}:")
    for hit in result.hits:
        print(f"  {hit.title}  [{hit.slug}]  загрузок: {format_downloads(hit.downloads)}")
        print(f"      {hit.description[:110]}")
    return 0


def cmd_modpack_install(manager: LauncherManager, args: argparse.Namespace) -> int:
    reporter = Reporter(ConsoleReporter(args.verbose))
    try:
        if args.source.startswith("http://") or args.source.startswith("https://"):
            import urllib.parse

            slug = urllib.parse.urlparse(args.source).path.rsplit("/", 1)[-1]
            instance, index = manager.download_modpack(slug, reporter=reporter)
        elif Path(args.source).is_file():
            instance, index = manager.install_modpack_file(
                args.source, display_name=args.name or "", reporter=reporter
            )
        else:
            instance, index = manager.download_modpack(
                args.source,
                game_version=args.version or "",
                loader=args.loader or "",
                reporter=reporter,
            )
    except (LauncherError, Cancelled) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Модпак «{index.name}» установлен как экземпляр «{instance.display_name}»")
    return 0


def cmd_doctor(manager: LauncherManager, args: argparse.Namespace) -> int:
    print(f"{APP_NAME} {APP_VERSION} — диагностика")
    print()
    problems = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal problems
        mark = "✓" if ok else "✗"
        if not ok:
            problems += 1
        print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))

    check("Каталог данных доступен для записи", _writable(manager.paths.root), str(manager.paths.root))
    runtimes = manager.java_runtimes()
    check("Найдена хотя бы одна Java", bool(runtimes), f"{len(runtimes)} шт.")

    try:
        manager.http.get_json(
            "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
        )
        check("Доступ к серверам Mojang", True)
    except Exception as error:  # noqa: BLE001
        check("Доступ к серверам Mojang", False, str(error))
    try:
        manager.modrinth.search("sodium", limit=1)
        check("Доступ к Modrinth", True)
    except Exception as error:  # noqa: BLE001
        check("Доступ к Modrinth", False, str(error))

    if manager.settings.effective_memory_mb > 3072:
        print(
            "  · Память выделена больше 3 ГБ — на хромбуках это может "
            "привести к тормозам системы."
        )
    print()
    print("Проблем не найдено." if problems == 0 else f"Найдено проблем: {problems}")
    return 0 if problems == 0 else 1


def _writable(path: Path) -> bool:
    try:
        probe = path / ".penguincraft-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def cmd_gui(manager: LauncherManager, args: argparse.Namespace) -> int:
    from .ui.app import run_app

    return run_app(manager)


# ---------------------------------------------------------------------------
# Разбор аргументов
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="penguincraft",
        description=f"{APP_NAME} {APP_VERSION} — лёгкий лаунчер Minecraft для Linux и ChromeOS",
    )
    parser.add_argument("--root", help="каталог данных лаунчера")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный вывод")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="запустить графический интерфейс")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("info", help="показать состояние лаунчера")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("doctor", help="проверить окружение")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("versions", help="список версий Minecraft")
    p.add_argument("--snapshots", action="store_true", help="включая снапшоты")
    p.add_argument("--old", action="store_true", help="включая устаревшие")
    p.add_argument("--refresh", action="store_true", help="обновить манифест")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_versions)

    p = sub.add_parser("install", help="установить версию/загрузчик")
    p.add_argument("version", help="версия Minecraft, например 1.20.1")
    p.add_argument("--loader", default="vanilla", choices=["vanilla", "fabric", "quilt", "forge", "neoforge"])
    p.add_argument("--loader-version", default="")
    p.add_argument("--instance", default="", help="экземпляр для дозагрузки файлов")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("instances", help="список экземпляров")
    p.set_defaults(func=cmd_instances)

    p = sub.add_parser("create", help="создать экземпляр")
    p.add_argument("name")
    p.add_argument("--version", required=True)
    p.add_argument("--loader", default="vanilla", choices=["vanilla", "fabric", "quilt", "forge", "neoforge"])
    p.add_argument("--loader-version", default="")
    p.add_argument("--icon", default="🎮")
    p.add_argument("--install", action="store_true", help="сразу скачать все файлы")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("launch", help="запустить экземпляр")
    p.add_argument("instance")
    p.add_argument("--offline-name", default="", help="использовать офлайн-профиль")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("accounts", help="список аккаунтов")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("account-add-offline", help="добавить офлайн-аккаунт")
    p.add_argument("name")
    p.set_defaults(func=cmd_account_add_offline)

    p = sub.add_parser("account-ms-login", help="войти через Microsoft")
    p.set_defaults(func=cmd_account_ms_login)

    p = sub.add_parser("account-elyby", help="войти через Ely.by")
    p.add_argument("username")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_account_elyby)

    p = sub.add_parser("mod-search", help="поиск модов на Modrinth")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--type", default="mod", choices=["mod", "modpack", "resourcepack", "shader"])
    p.add_argument("--loader", default="")
    p.add_argument("--version", default="")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_mod_search)

    p = sub.add_parser("modpack-install", help="установить модпак")
    p.add_argument("source", help="файл .mrpack, slug или ссылка Modrinth")
    p.add_argument("--name", default="")
    p.add_argument("--version", default="")
    p.add_argument("--loader", default="")
    p.set_defaults(func=cmd_modpack_install)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = LauncherManager(args.root)

    if not getattr(args, "command", None):
        if args.verbose:
            return cmd_info(manager, args)
        return cmd_gui(manager, args)
    return args.func(manager, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
