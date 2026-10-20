"""Тесты оценки свободного места и отказа от заведомо провальной установки."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core import storage
from penguincraft.core.installer import (
    ASSETS_FALLBACK_BYTES,
    estimate_missing_size,
)
from penguincraft.core.paths import Paths
from penguincraft.manager import LauncherError, LauncherManager


def version_payload() -> dict:
    return {
        "id": "1.20.1",
        "downloads": {
            "client": {
                "url": "https://example.invalid/client.jar",
                "size": 1000,
                "sha1": "0" * 40,
            }
        },
        "libraries": [
            {
                "name": "g:a:1",
                "downloads": {
                    "artifact": {
                        "path": "g/a/1/a-1.jar",
                        "url": "https://example.invalid/a-1.jar",
                        "size": 500,
                        "sha1": "0" * 40,
                    }
                },
            }
        ],
        "assetIndex": {"id": "5", "url": "https://example.invalid/5.json"},
        "assets": "5",
    }


class EstimateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = Paths(self._tmp.name)
        self.paths.ensure_base_dirs()

    def test_counts_missing_client_library_and_assets(self) -> None:
        needed = estimate_missing_size(self.paths, version_payload(), "1.20.1")
        self.assertEqual(needed, 1000 + 500 + ASSETS_FALLBACK_BYTES)

    def test_present_files_are_not_counted(self) -> None:
        client = self.paths.versions / "1.20.1" / "1.20.1.jar"
        client.parent.mkdir(parents=True, exist_ok=True)
        client.write_bytes(b"x" * 1000)
        library = self.paths.libraries / "g" / "a" / "1" / "a-1.jar"
        library.parent.mkdir(parents=True, exist_ok=True)
        library.write_bytes(b"x" * 500)
        index = self.paths.asset_indexes / "5.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"objects": {}}), encoding="utf-8")

        needed = estimate_missing_size(self.paths, version_payload(), "1.20.1")
        self.assertEqual(needed, 0)

    def test_missing_asset_object_is_counted(self) -> None:
        index = self.paths.asset_indexes / "5.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(
            json.dumps(
                {
                    "objects": {
                        "minecraft/sounds/a.ogg": {
                            "hash": "ab" * 20,
                            "size": 700,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        needed = estimate_missing_size(self.paths, version_payload(), "1.20.1")
        self.assertEqual(needed, 1000 + 500 + 700)


class ManagerSpaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = LauncherManager(self._tmp.name)

    def test_huge_install_raises_launcher_error(self) -> None:
        with self.assertRaises(LauncherError) as caught:
            self.manager._ensure_space(10 ** 18, "тестовую установку")
        message = str(caught.exception)
        self.assertIn("Недостаточно места", message)
        self.assertIn("тестовую установку", message)

    def test_small_install_passes(self) -> None:
        self.manager._ensure_space(1024, "мелочь")

    def test_storage_report_free_space(self) -> None:
        free, total = self.manager.free_space()
        self.assertGreater(free, 0)
        self.assertGreaterEqual(total, free)
        self.assertGreaterEqual(storage.format_size(free).count(" "), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
