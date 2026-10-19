"""Настройки лаунчера и профили аргументов JVM.

Значения по умолчанию подобраны под хромбуки: невысокое потребление
памяти, мягкий сборщик мусора, минимум фоновых потоков.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, List

from .paths import Paths
from .util import is_chromebook, total_memory_mb

# ---------------------------------------------------------------------------
# Профили JVM
# ---------------------------------------------------------------------------

#: Базовые аргументы, полезные почти всегда.
#:
#: Важно: ``-XX:+AlwaysPreTouch`` сюда сознательно не входит. Он заставляет
#: JVM сразу «прощупать» весь heap, и на слабых машинах/в контейнерах это
#: приводит к убийству процесса по памяти ещё до первого кадра.
COMMON_JVM_ARGS = [
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+UseCompressedOops",
    "-XX:+UseCompressedClassPointers",
    "-XX:-OmitStackTraceInFastThrow",
    "-XX:+ParallelRefProcEnabled",
    "-XX:+DisableExplicitGC",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxInlineLevel=15",
    "-Dfile.encoding=UTF-8",
]

#: Aikar-style флаги для G1: хороший баланс для Minecraft.
G1_ARGS = [
    "-XX:+UseG1GC",
    "-XX:G1HeapRegionSize=8M",
    "-XX:G1NewSizePercent=28",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:MaxGCPauseMillis=100",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:MaxTenuringThreshold=1",
]

#: Serial GC — самый дешёвый по CPU и памяти. Идеален для 2-ядерных
#: хромбуков и слабых Celeron'ов, а также для Java 8.
SERIAL_ARGS = [
    "-XX:+UseSerialGC",
    "-XX:MaxGCPauseMillis=80",
    "-XX:NewRatio=2",
]

#: Полностью отключаем то, что не нужно, и ускоряем старт.
LOW_END_ARGS = [
    "-XX:TieredStopAtLevel=1",  # меньше времени JIT, быстрее старт
    "-Dsun.java2d.opengl=false",
    "-Dorg.lwjgl.util.NoChecks=true",
]

JVM_PRESETS: Dict[str, Dict[str, Any]] = {
    "chromebook": {
        "title": "Хромбук (экономный)",
        "description": (
            "Serial GC, минимум фоновой работы, быстрый старт. "
            "Рекомендуется для 4 ГБ ОЗУ и 2 ядер."
        ),
        "args": COMMON_JVM_ARGS + SERIAL_ARGS + LOW_END_ARGS,
        "memory_ratio": 0.45,
        "max_memory_mb": 1536,
    },
    "balanced": {
        "title": "Сбалансированный",
        "description": "G1GC с флагами Aikar. Хороший выбор для 8 ГБ ОЗУ.",
        "args": COMMON_JVM_ARGS + G1_ARGS,
        "memory_ratio": 0.5,
        "max_memory_mb": 4096,
    },
    "performance": {
        "title": "Производительность",
        "description": "Максимум для модовых сборок на 8–16 ГБ ОЗУ.",
        "args": COMMON_JVM_ARGS + G1_ARGS + ["-XX:+AlwaysPreTouch"],
        "memory_ratio": 0.6,
        "max_memory_mb": 8192,
    },
    "vanilla_lite": {
        "title": "Ванилла без модов",
        "description": "Минимум лишнего: только то, что нужно ванильной игре.",
        "args": COMMON_JVM_ARGS[:1] + SERIAL_ARGS,
        "memory_ratio": 0.4,
        "max_memory_mb": 2048,
    },
    "custom": {
        "title": "Свой набор",
        "description": "Использовать только список аргументов ниже.",
        "args": [],
        "memory_ratio": 0.5,
        "max_memory_mb": 8192,
    },
}

JVM_PRESET_ORDER = ["chromebook", "balanced", "performance", "vanilla_lite", "custom"]

#: Флаги, которые есть не во всех JVM. Ключ — минимальная мажорная версия.
_MIN_JAVA: Dict[str, int] = {
    "-XX:+UseG1GC": 7,
    "-XX:G1HeapRegionSize": 7,
    "-XX:G1NewSizePercent": 8,
    "-XX:G1MaxNewSizePercent": 8,
    "-XX:G1ReservePercent": 7,
    "-XX:G1HeapWastePercent": 8,
    "-XX:G1MixedGCCountTarget": 8,
    "-XX:InitiatingHeapOccupancyPercent": 7,
    "-XX:G1MixedGCLiveThresholdPercent": 8,
    "-XX:G1RSetUpdatingPauseTimePercent": 9,
    "-XX:+UseCompressedClassPointers": 8,
    "-XX:MaxInlineLevel": 8,
    "-XX:TieredStopAtLevel": 8,
    "-XX:+UnlockExperimentalVMOptions": 8,
}

#: Флаги только для x86/x64.
_X86_ONLY = ("-XX:+UseFastStosb", "-XX:-UseCompressedOops")


def sanitize_jvm_args(
    args: List[str],
    *,
    java_major: int = 0,
    arch: str = "",
) -> List[str]:
    """Убирает флаги, которые данная JVM не поймёт.

    Это дешёвая страховка: лучше потерять пару оптимизаций, чем получить
    «Unrecognized VM option» вместо запущенной игры.
    """
    import platform as _platform

    arch = arch or _platform.machine().lower()
    is_x86 = arch in ("x86_64", "amd64", "i386", "i686", "x86")
    result: List[str] = []
    for arg in args:
        head = arg.split("=", 1)[0]
        if not is_x86 and head in _X86_ONLY:
            continue
        if java_major:
            minimum = _MIN_JAVA.get(head)
            if minimum is not None and java_major < minimum:
                continue
            # Java 8 не знает некоторых «современных» флагов.
            if java_major <= 8 and head in (
                "-XX:+UseZGC", "-XX:+ZGenerational", "-XX:G1RSetUpdatingPauseTimePercent",
            ):
                continue
        if head == "-XX:+AlwaysPreTouch" and java_major and java_major < 8:
            continue
        result.append(arg)
    return result


def auto_memory_mb(preset: str = "balanced") -> int:
    """Подбирает объём heap под конкретную машину."""
    preset_data = JVM_PRESETS.get(preset, JVM_PRESETS["balanced"])
    total = total_memory_mb()
    ratio = float(preset_data.get("memory_ratio", 0.5))
    cap = int(preset_data.get("max_memory_mb", 4096))
    floor = 512
    if total <= 4096:
        # на слабых машинах оставляем системе хотя бы 1.6 ГБ
        suggestion = min(int(total - 1600), int(total * ratio))
    else:
        suggestion = int(total * ratio)
    suggestion = max(floor, min(suggestion, cap))
    # округляем до 128 МБ — так аккуратнее в интерфейсе
    return max(floor, (suggestion // 128) * 128)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """Пользовательские настройки лаунчера."""

    # -- интерфейс
    theme: str = "dark"                      # dark | light
    window_width: int = 1180
    window_height: int = 760
    sidebar_width: int = 208

    # -- Java
    java_path: str = ""                      # пусто = автоматически
    auto_download_java: bool = True
    jvm_preset: str = "chromebook" if is_chromebook() else "balanced"
    custom_jvm_args: str = ""
    memory_mode: str = "auto"                # auto | manual
    memory_mb: int = 2048

    # -- игра
    resolution_width: int = 854
    resolution_height: int = 480
    fullscreen: bool = False
    demo_mode: bool = False
    game_args: str = ""
    force_lwjgl_software: bool = False        # LIBGL_ALWAYS_SOFTWARE=1

    # -- поведение
    close_on_launch: bool = False
    show_snapshots: bool = False
    show_old_versions: bool = False
    confirm_delete: bool = True
    parallel_downloads: int = 8
    proxy: str = ""

    # -- данные
    last_instance: str = "default"
    last_version: str = ""
    last_account: str = ""

    # -- служебное
    schema: int = 1

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, paths: Paths) -> "Settings":
        settings = cls()
        path = paths.settings
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            settings = cls.from_dict(raw)
        settings.sanitize()
        return settings

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        payload = {k: v for k, v in raw.items() if k in known}
        instance = cls(**payload)  # type: ignore[arg-type]
        return instance

    def save(self, paths: Paths) -> None:
        paths.root.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = paths.settings.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(paths.settings)

    # ------------------------------------------------------------------
    def sanitize(self) -> None:
        if self.jvm_preset not in JVM_PRESETS:
            self.jvm_preset = "balanced"
        if self.memory_mode not in ("auto", "manual"):
            self.memory_mode = "auto"
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        self.parallel_downloads = max(1, min(int(self.parallel_downloads or 8), 32))
        self.window_width = max(880, min(int(self.window_width or 1180), 4096))
        self.window_height = max(560, min(int(self.window_height or 760), 2400))
        self.resolution_width = max(320, min(int(self.resolution_width or 854), 7680))
        self.resolution_height = max(240, min(int(self.resolution_height or 480), 4320))
        if self.memory_mode == "manual":
            self.memory_mb = max(512, min(int(self.memory_mb or 2048), 65536))

    # ------------------------------------------------------------------
    @property
    def effective_memory_mb(self) -> int:
        if self.memory_mode == "manual":
            return max(512, int(self.memory_mb))
        return auto_memory_mb(self.jvm_preset)

    def jvm_arguments(self, java_major: int = 0) -> List[str]:
        """Итоговый набор JVM-флагов из профиля и доп. настроек."""
        preset = JVM_PRESETS.get(self.jvm_preset, JVM_PRESETS["balanced"])
        args: List[str] = list(preset.get("args", []))
        memory = self.effective_memory_mb
        args.append(f"-Xms{memory}M")
        args.append(f"-Xmx{memory}M")
        if self.custom_jvm_args.strip():
            args.extend(_split_args(self.custom_jvm_args))
        return sanitize_jvm_args(args, java_major=java_major)


# ---------------------------------------------------------------------------
# Профиль экземпляра
# ---------------------------------------------------------------------------


@dataclass
class InstanceConfig:
    """Настройки конкретного экземпляра (переопределяют глобальные)."""

    name: str = "default"
    display_name: str = "По умолчанию"
    version_id: str = ""
    game_version: str = ""
    loader: str = "vanilla"
    loader_version: str = ""
    icon: str = "🎮"
    notes: str = ""
    created_at: str = ""

    memory_mode: str = "inherit"      # inherit | auto | manual
    memory_mb: int = 0
    jvm_preset: str = "inherit"       # inherit | <preset>
    custom_jvm_args: str = ""
    java_path: str = ""
    resolution_width: int = 0
    resolution_height: int = 0
    fullscreen: bool = False
    game_args: str = ""
    isolated: bool = True             # отдельный game dir (у нас всегда отдельный)

    last_played: str = ""

    @classmethod
    def load(cls, path: Path) -> "InstanceConfig":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in raw.items() if k in known}
        config = cls(**filtered)  # type: ignore[arg-type]
        if not config.name:
            config.name = path.parent.name
        return config

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def memory_for(self, settings: Settings) -> int:
        if self.memory_mode == "manual" and self.memory_mb:
            return max(512, int(self.memory_mb))
        if self.memory_mode == "auto":
            return auto_memory_mb(self.jvm_preset if self.jvm_preset != "inherit" else settings.jvm_preset)
        return settings.effective_memory_mb

    def jvm_arguments(self, settings: Settings, java_major: int = 0) -> List[str]:
        preset_name = self.jvm_preset if self.jvm_preset != "inherit" else settings.jvm_preset
        preset = JVM_PRESETS.get(preset_name, JVM_PRESETS["balanced"])
        args: List[str] = list(preset.get("args", []))
        memory = self.memory_for(settings)
        args += [f"-Xms{memory}M", f"-Xmx{memory}M"]
        extra = self.custom_jvm_args.strip() or settings.custom_jvm_args.strip()
        if extra:
            args.extend(_split_args(extra))
        return sanitize_jvm_args(args, java_major=java_major)


def _split_args(text: str) -> List[str]:
    """Разбирает строку аргументов с поддержкой кавычек."""
    import shlex

    try:
        return shlex.split(text)
    except ValueError:
        return text.split()
