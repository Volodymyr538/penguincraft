#!/usr/bin/env bash
# Запуск PenguinCraft прямо из репозитория, без установки.
#
#   ./run.sh              — графический интерфейс
#   ./run.sh doctor       — проверка окружения
#   ./run.sh versions     — список версий Minecraft
#
# Каталог данных можно переопределить переменной PENGUINCRAFT_HOME.
set -euo pipefail

cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Не найден Python 3. Установите его: sudo apt install python3 python3-tk" >&2
    exit 1
fi

exec "$PYTHON" -m penguincraft "$@"
