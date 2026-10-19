"""Сборка Windows-дистрибутивов PenguinCraft.

Запускать **на Windows** (PyInstaller не умеет кросс-компиляцию):

    python packaging\\windows\\build.py

Что получается в ``dist/``::

    PenguinCraft-<версия>-portable.exe   один файл, ничего не устанавливает
    PenguinCraft-<версия>-setup.exe      установщик (Inno Setup)

Промежуточная папка ``build/windows/PenguinCraft/`` попадает в установщик.

Параметры::

    --portable-only   собрать только переносимый .exe (Inno Setup не нужен)
    --no-installer    то же самое
    --python PATH     какой интерпретатор звать для PyInstaller

Сторонние пакеты нужны только на этапе сборки (``pip install pyinstaller``),
в самом лаунчере по-прежнему лишь стандартная библиотека.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "windows"

# Пакет нужен только ради общей утилиты (UTF-8 в консоли Windows).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from penguincraft.core.util import ensure_utf8_console  # noqa: E402

LAUNCHER = WINDOWS / "launcher.py"
ICON = WINDOWS / "penguincraft.ico"
ISS = WINDOWS / "penguincraft.iss"

APP_NAME = "PenguinCraft"


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------


def read_version() -> str:
    text = (ROOT / "penguincraft" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("Не удалось определить версию в penguincraft/__init__.py")
    return match.group(1)


def run(command: list[str], **kwargs) -> None:
    print("$", " ".join(str(part) for part in command), flush=True)
    subprocess.check_call(command, **kwargs)


def find_iscc() -> Path | None:
    """Ищет компилятор Inno Setup (ISCC.exe)."""
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    candidates: list[Path] = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
            candidates.append(Path(base) / "Inno Setup 5" / "ISCC.exe")
    for path in candidates:
        if path.is_file():
            return path
    return None


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------


def pyinstaller_command(python: str, *, onefile: bool, distpath: Path, workpath: Path) -> list[str]:
    command = [
        python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",                       # без чёрного окна консоли
        "--name",
        APP_NAME,
        "--paths",
        str(ROOT),
        "--icon",
        str(ICON),
        "--add-data",
        f"{ROOT / 'penguincraft' / 'ui' / 'assets' / 'penguincraft.png'}"
        f"{os.pathsep}penguincraft/ui/assets",
        "--collect-submodules",
        "penguincraft",
        "--distpath",
        str(distpath),
        "--workpath",
        str(workpath),
        "--specpath",
        str(BUILD),
    ]
    # Тема оформления ttk лежит в стандартной библиотеке — PyInstaller её
    # подхватывает сам, но tkinter подтягиваем явно: он импортируется поздно.
    command += ["--hidden-import", "tkinter"]
    command.append("--onefile" if onefile else "--onedir")
    command.append(str(LAUNCHER))
    return command


def build_portable(python: str, version: str) -> Path:
    """Один файл, который можно просто запустить."""
    out = DIST / "portable"
    run(pyinstaller_command(python, onefile=True, distpath=out, workpath=BUILD / "_work_onefile"))
    produced = out / f"{APP_NAME}.exe"
    if not produced.is_file():
        raise SystemExit(f"PyInstaller не создал {produced}")
    target = DIST / f"{APP_NAME}-{version}-portable.exe"
    if target.exists():
        target.unlink()
    shutil.move(str(produced), str(target))
    shutil.rmtree(out, ignore_errors=True)
    return target


def build_onedir(python: str) -> Path:
    """Каталог для установщика: стартует заметно быстрее одного файла."""
    run(pyinstaller_command(python, onefile=False, distpath=BUILD, workpath=BUILD / "_work_onedir"))
    produced = BUILD / APP_NAME / f"{APP_NAME}.exe"
    if not produced.is_file():
        raise SystemExit(f"PyInstaller не создал {produced}")
    return BUILD / APP_NAME


def build_installer(version: str, source: Path) -> Path:
    iscc = find_iscc()
    if iscc is None:
        raise SystemExit(
            "Не найден ISCC.exe (Inno Setup). Установите Inno Setup 6 "
            "или запустите сборку с --portable-only."
        )
    DIST.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(iscc),
            f"/DMyAppVersion={version}",
            f"/DAppSource={source}",
            f"/DOutputRoot={DIST}",
            f"/DIconFile={ICON}",
            str(ISS),
        ]
    )
    target = DIST / f"{APP_NAME}-{version}-setup.exe"
    if not target.is_file():
        raise SystemExit(f"Inno Setup не создал {target}")
    return target


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_console()
    parser = argparse.ArgumentParser(description="Собрать PenguinCraft для Windows")
    parser.add_argument("--portable-only", "--no-installer", action="store_true",
                        dest="portable_only", help="только переносимый .exe")
    parser.add_argument("--python", default=sys.executable, help="интерпретатор для PyInstaller")
    args = parser.parse_args(argv)

    if not sys.platform.startswith("win"):
        print("Внимание: сборку .exe выполняют на Windows — здесь только подготовка.")

    version = read_version()
    print(f"PenguinCraft {version} — сборка для Windows")

    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    print("· иконка")
    run([args.python, str(WINDOWS / "make_icon.py")], cwd=str(WINDOWS))

    artifacts: list[Path] = [build_portable(args.python, version)]

    if not args.portable_only:
        print("· каталог приложения")
        source = build_onedir(args.python)
        print("· установщик")
        artifacts.append(build_installer(version, source))

    print("\nГотово:")
    for path in artifacts:
        size = path.stat().st_size if path.is_file() else 0
        print(f"  {path}  ({size / (1024 * 1024):.1f} МБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
