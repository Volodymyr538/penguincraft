"""Иконка приложения: PNG -> ICO без сторонних библиотек.

Используется только при сборке (Windows/.deb), в рантайм не попадает.

ICO со времён Windows Vista умеет хранить изображения в виде PNG — это
позволяет обойтись без декодирования и масштабирования картинки вручную
(обычный путь требует сторонней библиотеки вроде Pillow).  Мы кладём в
контейнер единственную запись 256×256; Windows сама уменьшает её для
мелких представлений.

Использование::

    python packaging/windows/make_icon.py [исходный.png] [целевой.ico]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "penguincraft" / "ui" / "assets" / "penguincraft.png"
DEFAULT_TARGET = Path(__file__).resolve().parent / "penguincraft.ico"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Ширина и высота из заголовка IHDR (без полного разбора PNG)."""
    if len(data) < 24 or data[:8] != _PNG_SIGNATURE:
        raise ValueError("Это не PNG-файл")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def build_ico(png_bytes: bytes) -> bytes:
    """Собирает ICO-контейнер с одной PNG-записью."""
    width, height = png_dimensions(png_bytes)
    # В ICO значение 0 означает 256 пикселей.
    dim_w = 0 if width >= 256 else width
    dim_h = 0 if height >= 256 else height

    header = struct.pack("<HHH", 0, 1, 1)  # reserved, type=icon, count
    entry = struct.pack(
        "<BBBBHHII",
        dim_w,          # ширина
        dim_h,          # высота
        0,              # число цветов палитры (0 = truecolor)
        0,              # зарезервировано
        1,              # слоёв (planes)
        32,             # бит на пиксель
        len(png_bytes),  # размер данных
        6 + 16,         # смещение данных (заголовок + одна запись)
    )
    return header + entry + png_bytes


def make_icon(source: Path = DEFAULT_SOURCE, target: Path = DEFAULT_TARGET) -> Path:
    data = Path(source).read_bytes()
    png_dimensions(data)  # проверяем формат до записи
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_bytes(build_ico(data))
    return Path(target)


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else DEFAULT_SOURCE
    target = Path(argv[2]) if len(argv) > 2 else DEFAULT_TARGET
    path = make_icon(source, target)
    print(f"Иконка создана: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
