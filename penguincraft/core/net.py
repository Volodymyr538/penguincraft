"""HTTP-клиент на стандартной библиотеке.

Особенности, важные именно для лаунчера:

* повторные попытки с экспоненциальной паузой (мобильные/слабые сети,
  Wi-Fi в школах и на хромбуках);
* атомарная догрузка файлов (``*.part`` + ``os.replace``);
* проверка контрольной суммы уже после скачивания;
* поддержка «докачки» по диапазону, если сервер её отдаёт;
* отмена через :class:`~penguincraft.core.task.Reporter`.
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .. import USER_AGENT
from .task import Reporter

DEFAULT_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 60.0
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

#: Размер куска при обычной загрузке.
CHUNK_SIZE = 1024 * 128
#: Размер куска при включённом ограничении скорости (плавнее прогресс).
THROTTLED_CHUNK_SIZE = 1024 * 32


class _RateLimiter:
    """Общий ограничитель скорости для всех загрузок одного :class:`Http`.

    Работает по принципу «виртуального расписания»: каждый кусок получает
    момент времени, когда он имеет право быть прочитанным.  Блокировка
    общая, поэтому параллельные загрузки делят один лимит, а не умножают
    его на число потоков.
    """

    def __init__(self, kbps: int = 0) -> None:
        self.bytes_per_second = max(0, int(kbps)) * 1024
        self._lock = threading.Lock()
        self._next = 0.0

    @property
    def enabled(self) -> bool:
        return self.bytes_per_second > 0

    def wait(self, amount: int, reporter: Optional[Reporter] = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + amount / self.bytes_per_second
            deadline = self._next
        # Спим короткими шагами, чтобы отмена срабатывала быстро.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if reporter is not None:
                reporter.check_cancel()
            time.sleep(min(remaining, 0.2))


class HttpError(Exception):
    """Ошибка HTTP с сохранением кода ответа."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} для {url}")
        self.status = status
        self.url = url
        self.body = body


class Http:
    """Простой синхронный HTTP-клиент."""

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 3,
        proxy: Optional[str] = None,
        limit_kbps: int = 0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.limiter = _RateLimiter(limit_kbps)
        self._context = ssl.create_default_context()
        handlers: list[urllib.request.BaseHandler] = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self._opener = urllib.request.build_opener(*handlers)

    @property
    def limit_kbps(self) -> int:
        return self.limiter.bytes_per_second // 1024

    def set_limit(self, kbps: int) -> None:
        """Меняет ограничение скорости (0 — без ограничения)."""
        self.limiter = _RateLimiter(kbps)

    # -- низкий уровень -----------------------------------------------------
    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ):
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept-Encoding", "gzip")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        return self._opener.open(request, timeout=timeout or self.timeout)

    def _read_all(self, response) -> bytes:
        raw = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        if encoding == "gzip":
            try:
                return gzip.decompress(raw)
            except (OSError, zlib.error):
                return raw
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return raw
        return raw

    def _with_retries(self, operation: Callable[[], Any], *, what: str) -> Any:
        last: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            try:
                return operation()
            except HttpError as error:
                if error.status not in RETRY_STATUS or attempt >= self.retries:
                    raise
                last = error
            except (urllib.error.URLError, socket.timeout, ConnectionError, ssl.SSLError, OSError, http.client.HTTPException) as error:
                last = error
                if attempt >= self.retries:
                    raise
            time.sleep(min(2.0 ** attempt, 8.0))
        raise last if last else RuntimeError(f"Не удалось выполнить {what}")

    # -- публичный API ------------------------------------------------------
    def get_bytes(self, url: str, headers: Optional[Mapping[str, str]] = None) -> bytes:
        def operation() -> bytes:
            try:
                with self.request(url, headers=headers) as response:
                    return self._read_all(response)
            except urllib.error.HTTPError as error:
                raise HttpError(error.code, url, error.read().decode("utf-8", "replace")) from error

        return self._with_retries(operation, what=url)

    def get_json(self, url: str, headers: Optional[Mapping[str, str]] = None) -> Any:
        raw = self.get_bytes(url, headers=headers)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Некорректный JSON от {url}: {error}") from error

    def post_json(
        self,
        url: str,
        payload: Any,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        body = json.dumps(payload).encode("utf-8")
        merged = {"Content-Type": "application/json", "Accept": "application/json"}
        merged.update(headers or {})

        def operation() -> Any:
            try:
                with self.request(url, method="POST", data=body, headers=merged) as response:
                    raw = self._read_all(response)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")
                raise HttpError(error.code, url, detail) from error
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return raw.decode("utf-8", "replace")

        return self._with_retries(operation, what=url)

    def post_form(
        self,
        url: str,
        fields: Mapping[str, str],
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        body = urllib.parse.urlencode(dict(fields)).encode("utf-8")
        merged = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        merged.update(headers or {})

        def operation() -> Any:
            try:
                with self.request(url, method="POST", data=body, headers=merged) as response:
                    raw = self._read_all(response)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")
                raise HttpError(error.code, url, detail) from error
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return raw.decode("utf-8", "replace")

        return self._with_retries(operation, what=url)

    # -- загрузка файлов ----------------------------------------------------
    def download(
        self,
        url: str,
        destination: Path | str,
        *,
        sha1: Optional[str] = None,
        size: Optional[int] = None,
        reporter: Optional[Reporter] = None,
        progress: Optional[Callable[[int], None]] = None,
        force: bool = False,
    ) -> Path:
        """Скачивает *url* в *destination* с докачкой.

        Недокачанный файл остаётся рядом как ``*.part``: при обрыве связи
        (или отмене) следующий запуск продолжит с того же места, а не
        начнёт заново.  Если сервер не поддерживает ``Range``, загрузка
        честно перезапустится с нуля.  Возвращает путь к файлу.
        """
        target = Path(destination)
        part = target.with_name(target.name + ".part")
        if force:
            try:
                part.unlink()
            except OSError:
                pass
        if target.exists() and not force:
            if size is not None and target.stat().st_size != size:
                pass
            elif sha1 is None or _quick_sha1(target) == sha1.lower():
                return target

        target.parent.mkdir(parents=True, exist_ok=True)

        def operation() -> Path:
            if reporter:
                reporter.check_cancel()
            offset = 0
            try:
                if part.is_file():
                    offset = part.stat().st_size
            except OSError:
                offset = 0
            # Уже скачанный полностью .part — просто переименовываем.
            if size and offset == size and not sha1:
                os.replace(part, target)
                return target
            if size and offset > size:
                try:
                    part.unlink()
                except OSError:
                    pass
                offset = 0

            request = urllib.request.Request(url)
            request.add_header("User-Agent", self.user_agent)
            request.add_header("Accept-Encoding", "identity")
            if offset:
                request.add_header("Range", f"bytes={offset}-")
            try:
                response = self._opener.open(request, timeout=DOWNLOAD_TIMEOUT)
            except urllib.error.HTTPError as error:
                if error.code != 416 or not offset:
                    detail = error.read().decode("utf-8", "replace")
                    error.close()
                    raise HttpError(error.code, url, detail) from error
                # .part не подошёл (файл на сервере изменился) — качаем заново.
                error.close()
                try:
                    part.unlink()
                except OSError:
                    pass
                offset = 0
                request = urllib.request.Request(url)
                request.add_header("User-Agent", self.user_agent)
                request.add_header("Accept-Encoding", "identity")
                response = self._opener.open(request, timeout=DOWNLOAD_TIMEOUT)
            with response:
                status = int(getattr(response, "status", 200) or 200)
                if offset and status != 206:
                    # Сервер проигнорировал Range — начинаем заново.
                    offset = 0
                declared = response.headers.get("Content-Length")
                length = int(declared) if declared and declared.isdigit() else 0
                total = (offset + length) if length else (size or 0)
                received = offset
                chunk_size = (
                    THROTTLED_CHUNK_SIZE if self.limiter.enabled else CHUNK_SIZE
                )
                with open(part, "ab" if offset else "wb") as handle:
                    while True:
                        if reporter:
                            reporter.check_cancel()
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        handle.write(chunk)
                        received += len(chunk)
                        self.limiter.wait(len(chunk), reporter)
                        if progress:
                            progress(received)
                        elif reporter and total:
                            reporter.progress(received, total)
            if size and part.stat().st_size != size:
                # Соединение оборвалось на середине: повторная попытка
                # продолжит с этого же места.
                raise ConnectionError(
                    f"неполная загрузка {url}: {part.stat().st_size} из {size} байт"
                )
            os.replace(part, target)
            return target

        result = self._with_retries(operation, what=url)

        if sha1:
            actual = _quick_sha1(target)
            if actual != sha1.lower():
                try:
                    target.unlink()
                except OSError:
                    pass
                raise ValueError(
                    f"Контрольная сумма не совпала для {target.name}: "
                    f"ожидалось {sha1}, получено {actual}"
                )
        return result

    def download_many(
        self,
        items: Iterable[tuple[str, Path | str]],
        *,
        reporter: Optional[Reporter] = None,
        parallel: int = 8,
    ) -> None:
        """Скачивает список (url, путь) в несколько потоков."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from .task import ProgressAggregator

        pending = list(items)
        if not pending:
            return
        aggregator = ProgressAggregator(reporter) if reporter else None
        if aggregator:
            aggregator.reset(len(pending), "Загрузка файлов")

        def one(index: int, url: str, path: Path | str) -> None:
            self.download(url, path, reporter=reporter)
            if aggregator:
                aggregator.update(str(index), 1)

        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            futures = [
                pool.submit(one, index, url, path)
                for index, (url, path) in enumerate(pending)
            ]
            for future in as_completed(futures):
                future.result()

    def open_stream(self, url: str, headers: Optional[Mapping[str, str]] = None):
        """Возвращает файловый объект ответа (для докачки/потоковой обработки)."""
        return self.request(url, headers=headers)


def _quick_sha1(path: Path) -> str:
    import hashlib

    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def urljoin(base: str, *parts: str) -> str:
    """Аккуратно склеивает URL с сохранением завершающего слэша в base."""
    if not base.endswith("/"):
        base += "/"
    return urllib.parse.urljoin(base, "/".join(p.strip("/") for p in parts))


def quote(value: str) -> str:
    return urllib.parse.quote(value)
