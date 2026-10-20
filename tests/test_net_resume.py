"""Тесты докачки загрузок и ограничения скорости."""

from __future__ import annotations

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core.net import _RateLimiter, Http


class _Server:
    """Мини-сервер, который умеет (или не умеет) ``Range``."""

    def __init__(
        self,
        payload: bytes,
        *,
        support_range: bool = True,
        reject_range: bool = False,
    ) -> None:
        self.payload = payload
        self.support_range = support_range
        self.reject_range = reject_range
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - имя задаёт http.server
                outer.requests.append(self.headers.get("Range") or "")
                start = 0
                raw = self.headers.get("Range") or ""
                if raw and outer.reject_range:
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if raw and outer.support_range:
                    try:
                        start = int(raw.split("=", 1)[1].split("-", 1)[0])
                    except (IndexError, ValueError):
                        start = 0
                body = outer.payload[start:]
                if start:
                    self.send_response(206)
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{len(outer.payload) - 1}/{len(outer.payload)}",
                    )
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/file.bin"


class DownloadResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.http = Http()
        self.payload = b"penguincraft-" * 4096  # ~57 КБ

    def test_starts_from_scratch(self) -> None:
        target = self.root / "file.bin"
        with _Server(self.payload) as server:
            self.http.download(server.url, target, size=len(self.payload))
            self.assertEqual(server.requests, [""])
        self.assertEqual(target.read_bytes(), self.payload)

    def test_resumes_from_part_file(self) -> None:
        target = self.root / "file.bin"
        part = target.with_name(target.name + ".part")
        half = len(self.payload) // 2
        part.write_bytes(self.payload[:half])

        with _Server(self.payload) as server:
            self.http.download(server.url, target, size=len(self.payload))
            self.assertEqual(server.requests, [f"bytes={half}-"])

        self.assertEqual(target.read_bytes(), self.payload)
        self.assertFalse(part.exists())

    def test_restarts_if_server_ignores_range(self) -> None:
        target = self.root / "file.bin"
        part = target.with_name(target.name + ".part")
        part.write_bytes(b"garbage" * 10)

        with _Server(self.payload, support_range=False) as server:
            self.http.download(server.url, target, size=len(self.payload))
            self.assertTrue(server.requests[0].startswith("bytes="))

        self.assertEqual(target.read_bytes(), self.payload)

    def test_restarts_if_server_rejects_range(self) -> None:
        target = self.root / "file.bin"
        part = target.with_name(target.name + ".part")
        part.write_bytes(b"stale" * 10)

        with _Server(self.payload, reject_range=True) as server:
            self.http.download(server.url, target, size=len(self.payload))
            self.assertEqual(len(server.requests), 2)

        self.assertEqual(target.read_bytes(), self.payload)

    def test_removes_file_on_checksum_mismatch(self) -> None:
        target = self.root / "file.bin"
        with _Server(self.payload) as server:
            with self.assertRaises(ValueError):
                self.http.download(server.url, target, sha1="0" * 40)
        self.assertFalse(target.exists())


class RateLimiterTests(unittest.TestCase):
    def test_disabled_is_instant(self) -> None:
        limiter = _RateLimiter(0)
        started = time.monotonic()
        limiter.wait(1024 * 1024)
        self.assertLess(time.monotonic() - started, 0.1)

    def test_limits_speed(self) -> None:
        limiter = _RateLimiter(64)  # 64 КБ/с
        started = time.monotonic()
        limiter.wait(8 * 1024)  # 8 КБ ≈ 0.125 с
        self.assertGreaterEqual(time.monotonic() - started, 0.08)

    def test_http_set_limit(self) -> None:
        http = Http(limit_kbps=128)
        self.assertEqual(http.limit_kbps, 128)
        http.set_limit(0)
        self.assertFalse(http.limiter.enabled)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
