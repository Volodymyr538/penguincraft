"""Отчёты о прогрессе, отмена операций и простые задачи.

Интерфейс (tkinter) не потокобезопасен, поэтому все фоновые операции
сообщают о своём состоянии через :class:`Reporter`, который лишь
складывает события в очередь.  UI-поток забирает их и обновляет виджеты.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class Cancelled(Exception):
    """Операция отменена пользователем."""


class TaskFailure(Exception):
    """Операция завершилась ошибкой, которую надо показать пользователю."""


# ---------------------------------------------------------------------------
# События
# ---------------------------------------------------------------------------


@dataclass
class Event:
    """Событие, публикуемое фоновой задачей."""

    kind: str  # status | progress | log | done | error
    text: str = ""
    current: float = 0.0
    total: float = 0.0
    detail: str = ""
    payload: Any = None


class Reporter:
    """Приёмник событий + точка проверки отмены.

    Может использоваться и синхронно (``emit=None`` — события падают в
    ``self.history``), что удобно для CLI и тестов.
    """

    def __init__(
        self,
        emit: Optional[Callable[[Event], None]] = None,
        *,
        throttle: float = 0.05,
    ) -> None:
        self._emit = emit
        self._cancel = threading.Event()
        self.history: list[Event] = []
        self._throttle = throttle
        self._last_progress = 0.0

    # -- отмена -------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    # -- публикация ---------------------------------------------------------
    def _publish(self, event: Event) -> None:
        if self._emit is None:
            self.history.append(event)
            return
        try:
            self._emit(event)
        except Exception:  # noqa: BLE001 - UI не должен ломать задачу
            pass

    def status(self, text: str) -> None:
        self._publish(Event("status", text=text))

    def log(self, text: str, detail: str = "") -> None:
        self._publish(Event("log", text=text, detail=detail))

    def progress(self, current: float, total: float, detail: str = "") -> None:
        now = time.monotonic()
        if total and current < total and (now - self._last_progress) < self._throttle:
            return
        self._last_progress = now
        self._publish(Event("progress", current=current, total=total, detail=detail))

    def done(self, payload: Any = None, text: str = "Готово") -> None:
        self._publish(Event("done", text=text, payload=payload))

    def error(self, text: str) -> None:
        self._publish(Event("error", text=text))

    def no_progress(self) -> "Reporter":
        """Копия отчёта, которая не транслирует прогресс.

        Нужна для параллельных загрузок: агрегатор уже считает общий
        прогресс, а отдельные потоки только создавали бы «дрожание».
        """

        parent = self

        class _Quiet(Reporter):
            def progress(self, current: float, total: float, detail: str = "") -> None:  # type: ignore[override]
                pass

            def status(self, text: str) -> None:  # type: ignore[override]
                parent.status(text)

            def log(self, text: str, detail: str = "") -> None:  # type: ignore[override]
                parent.log(text, detail)

            def done(self, payload: Any = None, text: str = "") -> None:  # type: ignore[override]
                pass

            def error(self, text: str) -> None:  # type: ignore[override]
                parent.error(text)

            def check_cancel(self) -> None:  # type: ignore[override]
                parent.check_cancel()

            def reset_cancel(self) -> None:  # type: ignore[override]
                pass

            @property
            def cancelled(self) -> bool:  # type: ignore[override]
                return parent.cancelled

        return _Quiet(emit=None)

    def child(self, *, start: float = 0.0, span: float = 1.0) -> "Reporter":
        """Дочерний отчёт, вкладывающий свой прогресс в шкалу ``[start, start+span]``.

        Родитель оперирует долями 0..1, а дочерний — своими единицами
        (например, количеством скачанных файлов).
        """

        parent = self

        class _Child(Reporter):
            def progress(self, current: float, total: float, detail: str = "") -> None:  # type: ignore[override]
                fraction = (current / total) if total else 0.0
                fraction = max(0.0, min(1.0, fraction))
                parent.progress(start + span * fraction, start + span, detail)

            def status(self, text: str) -> None:  # type: ignore[override]
                parent.status(text)

            def log(self, text: str, detail: str = "") -> None:  # type: ignore[override]
                parent.log(text, detail)

            def done(self, payload: Any = None, text: str = "") -> None:  # type: ignore[override]
                pass

            def error(self, text: str) -> None:  # type: ignore[override]
                parent.error(text)

            def check_cancel(self) -> None:  # type: ignore[override]
                parent.check_cancel()

            def reset_cancel(self) -> None:  # type: ignore[override]
                pass

            @property
            def cancelled(self) -> bool:  # type: ignore[override]
                return parent.cancelled

        return _Child(emit=self._emit)


# ---------------------------------------------------------------------------
# Многопоточный агрегатор прогресса
# ---------------------------------------------------------------------------


@dataclass
class ProgressAggregator:
    """Считает суммарный прогресс по нескольким параллельным загрузкам."""

    reporter: Reporter
    total_bytes: int = 0
    _done_bytes: int = 0
    _parts: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _base_status: str = ""

    def reset(self, total_bytes: int, status: str = "") -> None:
        with self._lock:
            self.total_bytes = total_bytes
            self._done_bytes = 0
            self._parts.clear()
            self._base_status = status

    def update(self, key: str, done: int) -> None:
        with self._lock:
            previous = self._parts.get(key, 0)
            self._parts[key] = done
            self._done_bytes += done - previous
            current = self._done_bytes
            total = self.total_bytes
        self.reporter.progress(current, total, self._base_status)

    def add_total(self, extra: int) -> None:
        with self._lock:
            self.total_bytes += extra
            total = self.total_bytes
        self.reporter.progress(self._done_bytes, total, self._base_status)


# ---------------------------------------------------------------------------
# Запуск в фоне
# ---------------------------------------------------------------------------


def run_in_thread(target: Callable[[], Any], name: str = "penguincraft-task") -> threading.Thread:
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread
