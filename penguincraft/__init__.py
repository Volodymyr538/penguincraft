"""
PenguinCraft — лёгкий лаунчер Minecraft для Linux / ChromeOS (Crostini).

Оптимизирован для хромбуков: минимум зависимостей (только стандартная
библиотека Python 3), нативный интерфейс на tkinter, авто-подбор и
загрузка нужной версии Java, агрессивные, но безопасные настройки JVM
для слабых машин.
"""

__all__ = ["APP_NAME", "APP_VERSION", "APP_ID", "USER_AGENT"]

APP_NAME = "PenguinCraft"
APP_ID = "penguincraft"
APP_VERSION = "1.2.0"

USER_AGENT = f"{APP_NAME}/{APP_VERSION} (+https://github.com/Volodymyr538/penguincraft)"
