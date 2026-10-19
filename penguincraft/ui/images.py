"""Кеш иконок проектов Modrinth.

Иконки — необязательное украшение: если сеть недоступна или Tk собран
без поддержки PNG, интерфейс просто останется без картинок.
"""

from __future__ import annotations

import base64
import threading
import tkinter as tk
from typing import Callable, Dict, Optional, Set

from ..core.net import Http


class IconCache:
    """Скачивает картинки в фоне и отдаёт готовые ``PhotoImage``."""

    def __init__(self, http: Http, size: int = 32) -> None:
        self.http = http
        self.size = size
        self._photos: Dict[str, tk.PhotoImage] = {}
        self._pending: Set[str] = set()
        self._failed: Set[str] = set()
        self._lock = threading.Lock()

    def get(
        self,
        url: str,
        on_ready: Callable[[tk.PhotoImage], None],
        schedule: Callable[[Callable[[], None]], None],
    ) -> Optional[tk.PhotoImage]:
        """Возвращает картинку из кеша или запускает загрузку в фоне."""
        if not url or url in self._failed:
            return None
        cached = self._photos.get(url)
        if cached is not None:
            return cached
        with self._lock:
            if url in self._pending:
                return None
            self._pending.add(url)

        def worker() -> None:
            try:
                data = self.http.get_bytes(url)
            except Exception:  # noqa: BLE001 - картинка не критична
                with self._lock:
                    self._pending.discard(url)
                    self._failed.add(url)
                return

            def build() -> None:
                try:
                    photo = tk.PhotoImage(
                        data=base64.b64encode(data).decode("ascii"),
                        format="png",
                    )
                    # Tk умеет только целочисленное уменьшение — берём
                    # делитель с округлением вверх, чтобы картинка точно
                    # поместилась в строку таблицы.
                    largest = max(photo.width(), photo.height())
                    factor = max(1, -(-largest // max(8, self.size)))
                    if factor > 1:
                        photo = photo.subsample(factor, factor)
                    self._photos[url] = photo
                    on_ready(photo)
                except Exception:  # noqa: BLE001 - нет поддержки PNG
                    self._failed.add(url)
                finally:
                    with self._lock:
                        self._pending.discard(url)

            schedule(build)

        threading.Thread(target=worker, name="penguincraft-icon", daemon=True).start()
        return None
