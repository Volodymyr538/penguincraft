"""Точка входа для сборки Windows-исполняемого файла (PyInstaller).

Запускать напрямую из исходников тоже можно::

    python packaging/windows/launcher.py

В собранном ``--windowed`` exe у процесса нет консоли, поэтому ``sys.stdout``
и ``sys.stderr`` равны ``None``.  Любой ``print`` в этом случае падал бы с
``AttributeError``, поэтому подменяем их на «пустоту».
"""

from __future__ import annotations

import os
import sys


def _silence_console() -> None:
    """Подставляет пустые потоки, если exe собран без консоли."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass
    if getattr(sys, "stdin", None) is None:
        try:
            sys.stdin = open(os.devnull, "r", encoding="utf-8")
        except OSError:
            pass


def main() -> int:
    _silence_console()
    from penguincraft.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
