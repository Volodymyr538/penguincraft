"""Мелкие утилиты: хеши, работа с архивами, имена файлов, maven-координаты."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Хеши
# ---------------------------------------------------------------------------

_CHUNK = 1024 * 256


def hash_file(path: Path | str, algorithm: str = "sha1") -> str:
    """Считает хеш файла, не загружая его целиком в память."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Архивы
# ---------------------------------------------------------------------------


def extract_zip(
    archive_path: Path | str,
    target_dir: Path | str,
    exclude: Iterable[str] = (),
    prefix_strip: str = "",
) -> None:
    """Распаковывает zip с защитой от path traversal."""
    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    excluded = tuple(exclude)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if excluded and name.startswith(excluded):
                continue
            if prefix_strip:
                if not name.startswith(prefix_strip):
                    continue
                name = name[len(prefix_strip):]
            if not name or name.endswith("/"):
                continue
            destination = (target / name).resolve()
            try:
                destination.relative_to(target)
            except ValueError:
                continue  # попытка выйти за пределы каталога — пропускаем
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)
            mode = info.external_attr >> 16
            if mode:
                try:
                    os.chmod(destination, mode & 0o777)
                except OSError:
                    pass


def extract_tar_gz(archive_path: Path | str, target_dir: Path | str) -> None:
    """Распаковывает .tar.gz с защитой от path traversal."""
    import tarfile

    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = []
        for member in archive.getmembers():
            destination = (target / member.name).resolve()
            try:
                destination.relative_to(target)
            except ValueError:
                continue
            if member.issym() or member.islnk():
                # символические ссылки в архивах JRE допустимы только
                # внутри каталога распаковки.
                link_target = (destination.parent / member.linkname).resolve()
                try:
                    link_target.relative_to(target)
                except ValueError:
                    continue
            members.append(member)
        archive.extractall(target, members=members)


# ---------------------------------------------------------------------------
# Maven-координаты
# ---------------------------------------------------------------------------

_MAVEN_RE = re.compile(
    r"^"
    r"(?P<group>[^:@]+):"
    r"(?P<artifact>[^:@]+):"
    r"(?P<version>[^:@]+)"
    r"(?::(?P<classifier>[^:@]+))?"
    r"(?:@(?P<extension>[^:@]+))?"
    r"$"
)


class MavenCoordinate:
    """Разбор строки вида ``group:artifact:version[:classifier][@ext]``."""

    __slots__ = ("group", "artifact", "version", "classifier", "extension", "raw")

    def __init__(
        self,
        group: str,
        artifact: str,
        version: str,
        classifier: str = "",
        extension: str = "jar",
        raw: str = "",
    ) -> None:
        self.group = group
        self.artifact = artifact
        self.version = version
        self.classifier = classifier
        self.extension = extension
        self.raw = raw or self.to_string()

    @classmethod
    def parse(cls, text: str) -> "MavenCoordinate":
        match = _MAVEN_RE.match(text.strip())
        if not match:
            raise ValueError(f"Неверные maven-координаты: {text!r}")
        return cls(
            group=match.group("group"),
            artifact=match.group("artifact"),
            version=match.group("version"),
            classifier=match.group("classifier") or "",
            extension=match.group("extension") or "jar",
            raw=text.strip(),
        )

    @property
    def file_name(self) -> str:
        base = f"{self.artifact}-{self.version}"
        if self.classifier:
            base += f"-{self.classifier}"
        return f"{base}.{self.extension}"

    @property
    def directory(self) -> str:
        return "/".join(self.group.split(".")) + f"/{self.artifact}/{self.version}"

    @property
    def path(self) -> str:
        return f"{self.directory}/{self.file_name}"

    def to_string(self) -> str:
        text = f"{self.group}:{self.artifact}:{self.version}"
        if self.classifier:
            text += f":{self.classifier}"
        if self.extension != "jar":
            text += f"@{self.extension}"
        return text

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"MavenCoordinate({self.to_string()!r})"


# ---------------------------------------------------------------------------
# Файлы и имена
# ---------------------------------------------------------------------------

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_name(name: str, fallback: str = "instance") -> str:
    """Приводит произвольную строку к безопасному имени каталога."""
    cleaned = _ILLEGAL.sub("_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]


def remove_tree(path: Path | str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def copy_tree(src: Path | str, dst: Path | str) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def human_size(num: float) -> str:
    """Человекочитаемый размер: 1234567 -> '1.2 МБ'."""
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(num) < 1024.0:
            if unit == "Б":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} ПБ"


# ---------------------------------------------------------------------------
# Система
# ---------------------------------------------------------------------------


def total_memory_mb() -> int:
    """Объём оперативной памяти в мегабайтах (для авто-подбора -Xmx)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        pass
    try:  # pragma: no cover - другие платформы
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 4096


def cpu_count() -> int:
    return os.cpu_count() or 2


def is_chromebook() -> bool:
    """Эвристика: запущены ли мы в контейнере Crostini на ChromeOS."""
    if os.environ.get("CROS_USER_ID_HASH"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8") as handle:
            return "chromeos" in handle.read().lower()
    except OSError:
        return False


def platform_arch() -> str:
    """Архитектура в терминах Mojang/Adoptium: x64, aarch64, x86."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    if machine.startswith("arm"):
        return "arm"
    return machine


def which(program: str) -> Optional[Path]:
    found = shutil.which(program)
    return Path(found) if found else None


def write_launcher_profiles(path: Path | str, version_id: str = "penguincraft") -> Path:
    """Создаёт минимальный ``launcher_profiles.json``.

    Установщики Forge/NeoForge отказываются работать без него.
    """
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profiles": {
            version_id: {
                "name": version_id,
                "lastVersionId": version_id,
                "type": "custom",
            }
        },
        "selectedProfile": version_id,
        "clientToken": "penguincraft-launcher",
        "authenticationDatabase": {},
        "launcherVersion": {"name": "penguincraft", "format": 21},
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def open_in_file_manager(path: Path | str) -> None:
    """Открывает каталог в системном файловом менеджере."""
    directory = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(directory)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", directory])
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, directory])
    except OSError:
        pass
