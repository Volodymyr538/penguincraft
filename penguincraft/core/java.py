"""Поиск, определение и автоматическая загрузка Java.

На хромбуках Java обычно не установлена (или стоит только один выпуск),
поэтому лаунчер умеет сам скачивать нужный JRE с Adoptium (Eclipse
Temurin) — это официально поддерживаемый и свободный дистрибутив.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .net import Http
from .paths import Paths
from .task import Reporter
from .util import extract_tar_gz, platform_arch

ADOPTIUM_BINARY = (
    "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/jre/hotspot/normal/eclipse"
)
_ADOPTIUM_OS = {"linux": "linux", "windows": "windows", "osx": "mac"}

_VERSION_RE = re.compile(r'version "([^"]+)"')


@dataclass
class JavaRuntime:
    """Найденная или скачанная среда выполнения."""

    path: Path                     # исполняемый файл java
    version: str = ""              # полная строка версии
    major: int = 0
    arch: str = ""
    vendor: str = ""
    source: str = "system"         # system | java_home | downloaded | manual
    managed: bool = False

    @property
    def home(self) -> Path:
        return self.path.parent.parent

    @property
    def bitness(self) -> int:
        return 64 if self.arch in ("x64", "aarch64", "arm64") else 32

    @property
    def title(self) -> str:
        parts = [f"Java {self.major or '?'}"]
        if self.vendor:
            parts.append(self.vendor)
        if self.arch:
            parts.append(self.arch)
        parts.append(f"({self.source})")
        return " · ".join(parts)

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "version": self.version,
            "major": self.major,
            "arch": self.arch,
            "vendor": self.vendor,
            "source": self.source,
            "managed": self.managed,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, object]) -> "JavaRuntime":
        return cls(
            path=Path(str(raw.get("path", ""))),
            version=str(raw.get("version", "")),
            major=int(raw.get("major", 0) or 0),
            arch=str(raw.get("arch", "")),
            vendor=str(raw.get("vendor", "")),
            source=str(raw.get("source", "system")),
            managed=bool(raw.get("managed", False)),
        )

    def run(self, args: Iterable[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


# ---------------------------------------------------------------------------
# Определение версии
# ---------------------------------------------------------------------------


def parse_major(version_text: str) -> int:
    """``1.8.0_402`` -> 8, ``21.0.12`` -> 21, ``17`` -> 17."""
    text = version_text.strip()
    if not text:
        return 0
    if text.startswith("1."):
        parts = text.split(".")
        if len(parts) > 1 and parts[1].isdigit():
            return int(parts[1])
    match = re.match(r"^(\d+)", text)
    return int(match.group(1)) if match else 0


def probe_java(executable: Path | str, source: str = "system", managed: bool = False) -> Optional[JavaRuntime]:
    """Запускает ``java -version`` и разбирает результат."""
    executable = Path(executable)
    if not executable.is_file():
        return None
    try:
        process = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (process.stderr or "") + (process.stdout or "")
    if process.returncode != 0 and "version" not in output:
        return None
    match = _VERSION_RE.search(output)
    version = match.group(1) if match else ""
    if not version:
        return None
    major = parse_major(version)
    vendor = ""
    lowered = output.lower()
    for key, name in (
        ("temurin", "Temurin"),
        ("adoptium", "Temurin"),
        ("zulu", "Zulu"),
        ("corretto", "Corretto"),
        ("oracle", "Oracle"),
        ("graalvm", "GraalVM"),
        ("microsoft", "Microsoft"),
        ("openjdk", "OpenJDK"),
    ):
        if key in lowered:
            vendor = name
            break
    arch = _release_arch(executable) or platform_arch()
    return JavaRuntime(
        path=executable,
        version=version,
        major=major,
        arch=arch,
        vendor=vendor,
        source=source,
        managed=managed,
    )


def _release_arch(executable: Path) -> str:
    release = executable.parent.parent / "release"
    if not release.is_file():
        return ""
    try:
        for line in release.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("OS_ARCH="):
                value = line.split("=", 1)[1].strip().strip('"').lower()
                if value in ("amd64", "x86_64"):
                    return "x64"
                if value in ("aarch64", "arm64"):
                    return "aarch64"
                return value
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Поиск установленных JVM
# ---------------------------------------------------------------------------


def _candidate_paths() -> List[Tuple[Path, str]]:
    candidates: List[Tuple[Path, str]] = []

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append((Path(java_home) / "bin" / "java", "java_home"))

    from shutil import which

    on_path = which("java")
    if on_path:
        candidates.append((Path(on_path), "system"))

    # Linux-специфичные каталоги (Debian/Ubuntu, Crostini, Fedora, Arch)
    roots = [
        Path("/usr/lib/jvm"),
        Path("/usr/java"),
        Path("/opt/java"),
        Path("/opt"),
        Path.home() / ".jdks",
        Path.home() / ".sdkman" / "candidates" / "java",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), reverse=True)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            for relative in ("bin/java", "Contents/Home/bin/java", "jre/bin/java"):
                candidate = entry / relative
                if candidate.is_file():
                    candidates.append((candidate, "system"))
    return candidates


def find_installed_java(paths: Optional[Paths] = None) -> List[JavaRuntime]:
    """Все найденные JVM: системные и скачанные лаунчером."""
    found: List[JavaRuntime] = []
    seen: set[str] = set()

    # 1. Управляемые (скачанные) рантаймы
    if paths is not None:
        for runtime in list_managed_runtimes(paths):
            key = str(runtime.path.resolve()) if runtime.path.exists() else str(runtime.path)
            if key not in seen:
                seen.add(key)
                found.append(runtime)

    # 2. Системные
    for candidate, source in _candidate_paths():
        try:
            key = str(candidate.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        runtime = probe_java(candidate, source=source)
        if runtime is not None:
            found.append(runtime)

    found.sort(key=lambda item: (item.major, item.managed), reverse=True)
    return found


# ---------------------------------------------------------------------------
# Управляемые рантаймы
# ---------------------------------------------------------------------------


def managed_index(paths: Paths) -> Path:
    return paths.runtimes / "runtimes.json"


def list_managed_runtimes(paths: Paths) -> List[JavaRuntime]:
    index = managed_index(paths)
    runtimes: List[JavaRuntime] = []
    if index.is_file():
        try:
            raw = json.loads(index.read_text(encoding="utf-8"))
            for item in raw.get("runtimes", []):
                runtime = JavaRuntime.from_dict(item)
                if runtime.path.is_file():
                    runtimes.append(runtime)
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    if not runtimes and paths.runtimes.is_dir():
        # Резервный поиск, если индекс потерялся.
        for entry in sorted(paths.runtimes.iterdir()):
            if not entry.is_dir():
                continue
            for relative in ("bin/java", "Contents/Home/bin/java"):
                candidate = entry / relative
                if candidate.is_file():
                    runtime = probe_java(candidate, source="downloaded", managed=True)
                    if runtime:
                        runtimes.append(runtime)
    return runtimes


def save_managed_runtimes(paths: Paths, runtimes: Iterable[JavaRuntime]) -> None:
    payload = {"runtimes": [runtime.to_dict() for runtime in runtimes]}
    index = managed_index(paths)
    index.parent.mkdir(parents=True, exist_ok=True)
    tmp = index.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(index)


def _adoptium_url(major: int, arch: str) -> str:
    import platform as _platform

    system = _platform.system().lower()
    os_name = _ADOPTIUM_OS.get("osx" if system == "darwin" else system, "linux")
    if arch not in ("x64", "aarch64"):
        arch = "x64"
    return ADOPTIUM_BINARY.format(major=major, os=os_name, arch=arch)


def download_java(
    paths: Paths,
    major: int,
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    arch: str = "",
) -> JavaRuntime:
    """Скачивает и распаковывает JRE нужной мажорной версии."""
    arch = arch or platform_arch()
    url = _adoptium_url(major, arch)
    if reporter:
        reporter.status(f"Загрузка Java {major} (Temurin, ~45 МБ)…")

    paths.runtimes.mkdir(parents=True, exist_ok=True)
    archive = paths.downloads / f"jre-{major}-{arch}.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    http.download(url, archive, reporter=reporter)

    staging = paths.runtimes / f".staging-{major}"
    if staging.exists():
        from .util import remove_tree

        remove_tree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    if reporter:
        reporter.status(f"Распаковка Java {major}…")

    try:
        if archive.suffixes[-2:] == [".tar", ".gz"] or archive.name.endswith(".tar.gz"):
            extract_tar_gz(archive, staging)
        else:  # pragma: no cover - на случай zip-сборок
            from .util import extract_zip

            extract_zip(archive, staging)
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError(f"Не удалось распаковать Java: {error}") from error

    java_exe = _find_java_in(staging)
    if java_exe is None:
        raise RuntimeError("В скачанном архиве Java не найден исполняемый файл")

    target = paths.runtime_dir(major)
    from .util import remove_tree

    if target.exists():
        remove_tree(target)
    top = java_exe.parent.parent
    if top.parent == staging:
        # обычный случай: <staging>/jdk-.../bin/java
        top.rename(target)
        remove_tree(staging)
    else:
        staging.rename(target)
    java_exe = target / java_exe.relative_to(top)

    runtime = probe_java(java_exe, source="downloaded", managed=True)
    if runtime is None:
        raise RuntimeError("Скачанная Java не запускается")
    runtime.major = major
    runtimes = [item for item in list_managed_runtimes(paths) if item.major != major]
    runtimes.append(runtime)
    save_managed_runtimes(paths, runtimes)

    try:
        archive.unlink()
    except OSError:
        pass
    return runtime


def _find_java_in(root: Path) -> Optional[Path]:
    for relative in ("bin/java", "Contents/Home/bin/java"):
        direct = root / relative
        if direct.is_file():
            return direct
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            found = _find_java_in(entry)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Выбор подходящей Java
# ---------------------------------------------------------------------------


def select_java(
    runtimes: Iterable[JavaRuntime],
    required_major: int,
    *,
    preferred_path: str = "",
) -> Optional[JavaRuntime]:
    """Подбирает наиболее подходящую JVM.

    Приоритет: точное совпадение мажорной версии → более новая (но не
    слишком, Minecraft ломается на слишком новых) → любая.
    """
    candidates = list(runtimes)
    if not candidates:
        return None
    if preferred_path:
        preferred = Path(preferred_path).expanduser()
        for runtime in candidates:
            if runtime.path == preferred or str(runtime.path) == str(preferred):
                return runtime
        probed = probe_java(preferred, source="manual")
        if probed is not None:
            return probed

    if required_major:
        exact = [item for item in candidates if item.major == required_major]
        if exact:
            # предпочитаем 64-битную и скачанную лаунчером
            exact.sort(key=lambda item: (item.bitness, item.managed), reverse=True)
            return exact[0]

        compatible = [item for item in candidates if item.major >= required_major]
        if compatible:
            compatible.sort(key=lambda item: item.major)
            return compatible[0]

    candidates.sort(key=lambda item: item.major, reverse=True)
    return candidates[0]


def ensure_java(
    paths: Paths,
    required_major: int,
    http: Http,
    *,
    reporter: Optional[Reporter] = None,
    preferred_path: str = "",
    allow_download: bool = True,
) -> JavaRuntime:
    """Возвращает подходящую Java, при необходимости скачивая её."""
    runtimes = find_installed_java(paths)
    chosen = select_java(runtimes, required_major, preferred_path=preferred_path)
    if chosen is not None and (not required_major or chosen.major == required_major):
        return chosen
    if not allow_download:
        if chosen is not None:
            return chosen
        raise RuntimeError(
            f"Не найдена Java {required_major}. Установите её командой "
            f"«sudo apt install openjdk-{required_major}-jre-headless» "
            "или включите авто-загрузку Java в настройках."
        )
    if reporter:
        reporter.status(f"Подходящая Java {required_major} не найдена — скачиваем…")
    runtime = download_java(paths, required_major, http, reporter=reporter)
    return runtime


# ---------------------------------------------------------------------------
# Проверка JVM-флагов
# ---------------------------------------------------------------------------

_UNRECOGNIZED_RE = re.compile(
    r"(?:Unrecognized VM option|Unrecognized option|Improperly specified VM option)"
    r"\s*'?([^\s'\]\)]+)'?"
)
_IMPROPER_RE = re.compile(r"Improperly specified VM option\s*'([^']+)'")
_CACHE_FILE = "jvm-args.json"


def _args_cache_path(paths: Paths) -> Path:
    return paths.meta / _CACHE_FILE


def _load_args_cache(paths: Paths) -> Dict[str, List[str]]:
    path = _args_cache_path(paths)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): list(v) for k, v in (data.get("entries") or {}).items()}


def _save_args_cache(paths: Paths, entries: Dict[str, List[str]]) -> None:
    path = _args_cache_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps({"entries": entries}, indent=1), encoding="utf-8"
        )
    except OSError:
        pass


def probe_jvm_args(
    java: JavaRuntime,
    args: Iterable[str],
    *,
    paths: Optional[Paths] = None,
) -> List[str]:
    """Возвращает подмножество *args*, которое данная JVM действительно понимает.

    Флаги, на которых JVM отказывается стартовать, отбрасываются
    по одному.  Результат кешируется, поэтому проверка выполняется
    один раз на конфигурацию.
    """
    import hashlib

    arg_list = [str(item) for item in args]
    if not arg_list:
        return []

    try:
        stamp = f"{java.path.stat().st_mtime_ns}:{java.path.stat().st_size}"
    except OSError:
        stamp = str(java.path)
    key = hashlib.sha1(
        (stamp + "\u0000" + "\u0000".join(arg_list)).encode("utf-8")
    ).hexdigest()
    cache: Dict[str, List[str]] = {}
    if paths is not None:
        cache = _load_args_cache(paths)
        if key in cache:
            return cache[key]

    remaining = list(arg_list)
    for _ in range(12):
        try:
            process = subprocess.run(
                [str(java.path), *remaining, "-version"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return arg_list
        if process.returncode == 0:
            if paths is not None:
                cache[key] = remaining
                _save_args_cache(paths, cache)
            return remaining
        output = (process.stderr or "") + (process.stdout or "")
        match = _UNRECOGNIZED_RE.search(output)
        if not match:
            # Не поняли причину — не рискуем и убираем все свои флаги.
            if paths is not None:
                cache[key] = []
                _save_args_cache(paths, cache)
            return []
        bad = match.group(1)
        dropped = False
        for item in list(remaining):
            head = item.split("=", 1)[0]
            if head == bad or item == bad or head.startswith(bad):
                remaining.remove(item)
                dropped = True
        if not dropped:
            remaining = [item for item in remaining if bad not in item]
            if not remaining:
                return []
        if not remaining:
            if paths is not None:
                cache[key] = []
                _save_args_cache(paths, cache)
            return []
    return remaining
