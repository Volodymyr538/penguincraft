"""Сборка командной строки запуска и управление процессом игры."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from .. import APP_NAME, APP_VERSION
from .installer import InstallPlan
from .paths import Paths
from .rules import default_platform, resolve_arguments, substitute_all
from .settings import InstanceConfig, Settings
from .util import split_args as _split_args


class LaunchError(Exception):
    """Не удалось запустить игру."""


@dataclass
class LaunchCommand:
    """Готовая команда запуска."""

    argv: List[str]
    cwd: Path
    env: Dict[str, str]
    plan: InstallPlan
    variables: Dict[str, str] = field(default_factory=dict)
    log_file: Optional[Path] = None

    @property
    def pretty(self) -> str:
        return " ".join(shlex.quote(part) for part in self.argv)


# ---------------------------------------------------------------------------
# Построение команды
# ---------------------------------------------------------------------------


def _features(settings: Settings, instance: InstanceConfig) -> Dict[str, bool]:
    return {
        "is_demo_user": bool(settings.demo_mode),
        "has_custom_resolution": True,
        "has_quick_plays_support": False,
        "is_quick_play_singleplayer": False,
        "is_quick_play_multiplayer": False,
        "is_quick_play_realms": False,
    }


def _variables(
    paths: Paths,
    settings: Settings,
    instance: InstanceConfig,
    account: Any,
    plan: InstallPlan,
) -> Dict[str, str]:
    version = plan.version
    index_id = ""
    if plan.assets is not None:
        index_id = plan.assets.index_id
    if not index_id:
        index_id = str(version.get("assets") or "legacy")

    game_dir = Path(plan.game_dir)
    width = instance.resolution_width or settings.resolution_width
    height = instance.resolution_height or settings.resolution_height

    identity = account.launch_identity() if account is not None else {}
    variables: Dict[str, str] = {
        "natives_directory": str(plan.natives_dir or paths.natives_dir(plan.version_id)),
        "launcher_name": APP_NAME,
        "launcher_version": APP_VERSION,
        "classpath": plan.classpath_string,
        "classpath_separator": os.pathsep,
        "library_directory": str(paths.libraries),
        "version_name": plan.version_id,
        "version_type": str(version.get("type") or "release"),
        "game_directory": str(game_dir),
        "game_assets": str(plan.assets.virtual_dir) if plan.assets and plan.assets.virtual_dir else "",
        "assets_root": str(paths.assets),
        "assets_index_name": index_id,
        "resolution_width": str(width),
        "resolution_height": str(height),
        "quickPlayPath": str(paths.meta / "quickPlay" / "quickPlay.log"),
        "quickPlaySingleplayer": "",
        "quickPlayMultiplayer": "",
        "quickPlayRealms": "",
        "auth_player_name": "",
        "auth_uuid": "",
        "auth_access_token": "0",
        "auth_session": "0",
        "auth_xuid": "",
        "user_type": "legacy",
        "user_properties": "{}",
        "clientid": "",
        "assets_index": index_id,
    }
    variables.update({str(k): str(v) for k, v in identity.items()})
    if not variables.get("auth_session"):
        variables["auth_session"] = variables.get("auth_access_token", "0")
    return variables


def build_command(
    paths: Paths,
    settings: Settings,
    instance: InstanceConfig,
    account: Any,
    plan: InstallPlan,
    *,
    extra_jvm_args: Optional[List[str]] = None,
    extra_game_args: Optional[List[str]] = None,
) -> LaunchCommand:
    """Собирает полную командную строку запуска Minecraft."""
    if plan.java is None:
        raise LaunchError("Не выбрана Java для запуска")

    version = plan.version
    features = _features(settings, instance)
    platform = default_platform(features)
    variables = _variables(paths, settings, instance, account, plan)

    # --- аргументы JVM из описания версии --------------------------------
    version_args = version.get("arguments")
    if version_args:
        jvm_args = resolve_arguments(version_args.get("jvm"), platform)
        game_args = resolve_arguments(version_args.get("game"), platform)
    else:
        legacy = str(version.get("minecraftArguments") or "")
        game_args = legacy.split() if legacy else []
        jvm_args = []

    jvm_args = substitute_all(jvm_args, variables)
    game_args = substitute_all(game_args, variables)

    if not version_args:
        # Старые версии не описывают JVM-аргументы — добавляем сами.
        jvm_args = [
            f"-Djava.library.path={variables['natives_directory']}",
            "-cp",
            variables["classpath"],
        ]

    # --- аргументы JVM лаунчера -------------------------------------------
    java_major = plan.java.major if plan.java else 0
    launcher_jvm: List[str] = []
    launcher_jvm.extend(instance.jvm_arguments(settings, java_major))

    logging = version.get("logging") or {}
    if plan.logging_config is not None and isinstance(logging, Mapping):
        client = logging.get("client") or {}
        template = str(client.get("argument") or "")
        if template:
            launcher_jvm.append(template.replace("${path}", str(plan.logging_config)))

    if not any(arg.startswith("-Dminecraft.launcher.brand") for arg in jvm_args):
        launcher_jvm.append(f"-Dminecraft.launcher.brand={APP_NAME}")
    if not any(arg.startswith("-Dminecraft.launcher.version") for arg in jvm_args):
        launcher_jvm.append(f"-Dminecraft.launcher.version={APP_VERSION}")
    if settings.demo_mode:
        launcher_jvm.append("-Dminecraft.demo=true")
    launcher_jvm.extend(extra_jvm_args or [])

    # Отбрасываем флаги, которые не понимает выбранная JVM.
    if plan.java is not None:
        from .java import probe_jvm_args

        launcher_jvm = probe_jvm_args(plan.java, launcher_jvm, paths=paths)

    main_class = str(version.get("mainClass") or "net.minecraft.client.main.Main")

    # --- сборка -----------------------------------------------------------
    argv: List[str] = [str(plan.java.path)]
    argv.extend(launcher_jvm)
    argv.extend(jvm_args)
    argv.append(main_class)
    argv.extend(game_args)

    if settings.fullscreen or instance.fullscreen:
        argv.append("--fullscreen")

    custom_game = (instance.game_args or "").strip() or (settings.game_args or "").strip()
    if custom_game:
        argv.extend(_split_args(custom_game))
    argv.extend(extra_game_args or [])

    # --- окружение --------------------------------------------------------
    env = dict(os.environ)
    env.setdefault("JAVA_HOME", str(plan.java.home))
    if settings.force_lwjgl_software:
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    if not sys.platform.startswith("win"):
        # Переменные Mesa имеют смысл только под Linux (Crostini/слабые GPU).
        env["MESA_LOADER_DRIVER_OVERRIDE"] = env.get("MESA_LOADER_DRIVER_OVERRIDE", "")

    log_dir = paths.logs
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{instance.name}-{time.strftime('%Y%m%d-%H%M%S')}.log"

    return LaunchCommand(
        argv=argv,
        cwd=Path(plan.game_dir),
        env=env,
        plan=plan,
        variables=variables,
        log_file=log_file,
    )


# ---------------------------------------------------------------------------
# Запуск процесса
# ---------------------------------------------------------------------------


class GameProcess:
    """Обёртка над процессом игры: логи, завершение, остановка."""

    def __init__(
        self,
        process: subprocess.Popen,
        command: LaunchCommand,
        *,
        on_line: Optional[Callable[[str], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.process = process
        self.command = command
        self.on_line = on_line
        self.on_exit = on_exit
        self.started_at = time.time()
        self.exit_code: Optional[int] = None
        self._log_handle = None
        self._threads: List[threading.Thread] = []
        self._start()

    def _start(self) -> None:
        if self.command.log_file is not None:
            logs = self.command.log_file
            logs.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = open(logs, "a", encoding="utf-8", errors="replace")
            self._log_handle.write(
                f"# {APP_NAME} {APP_VERSION}\n# {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"# {self.command.pretty}\n\n"
            )
            self._log_handle.flush()
        reader = threading.Thread(target=self._pump, name="penguincraft-log", daemon=True)
        reader.start()
        self._threads.append(reader)

    def _pump(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, ""):
                line = raw.rstrip("\n")
                if self._log_handle is not None:
                    try:
                        self._log_handle.write(line + "\n")
                        self._log_handle.flush()
                    except (OSError, ValueError):
                        pass
                if self.on_line is not None:
                    try:
                        self.on_line(line)
                    except Exception:  # noqa: BLE001
                        pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    def wait(self) -> int:
        code = self.process.wait()
        self.exit_code = code
        for thread in self._threads:
            thread.join(timeout=2.0)
        if self._log_handle is not None:
            try:
                self._log_handle.write(f"\n# Процесс завершён с кодом {code}\n")
                self._log_handle.close()
            except (OSError, ValueError):
                pass
        if self.on_exit is not None:
            try:
                self.on_exit(code)
            except Exception:  # noqa: BLE001
                pass
        return code

    def stop(self, timeout: float = 8.0) -> None:
        """Мягко останавливает игру (как кнопкой «Выйти из игры»)."""
        if not self.running:
            return
        try:
            self.process.terminate()
        except OSError:
            return
        try:
            self.process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            self.process.kill()
        except OSError:
            pass

    def crash_report(self) -> Optional[Path]:
        """Свежий crash-report, если игра упала."""
        directory = Path(self.command.plan.game_dir) / "crash-reports"
        if not directory.is_dir():
            return None
        newest: Optional[Path] = None
        newest_time = 0.0
        for entry in directory.glob("crash-*.txt"):
            try:
                modified = entry.stat().st_mtime
            except OSError:
                continue
            if modified > newest_time:
                newest = entry
                newest_time = modified
        if newest is not None and newest_time >= self.started_at - 5:
            return newest
        return None


def spawn(command: LaunchCommand) -> GameProcess:
    """Запускает игру отдельным процессом."""
    creationflags = 0
    new_session = True
    if sys.platform.startswith("win"):  # pragma: no cover - только Windows
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        new_session = False  # start_new_session — только для POSIX
    try:
        process = subprocess.Popen(
            command.argv,
            cwd=str(command.cwd),
            env=command.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=new_session,
            creationflags=creationflags,
        )
    except FileNotFoundError as error:
        raise LaunchError(f"Не найден исполняемый файл: {error}") from error
    except OSError as error:
        raise LaunchError(f"Не удалось запустить игру: {error}") from error
    return GameProcess(process, command)
