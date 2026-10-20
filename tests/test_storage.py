"""Тесты экрана хранилища: размеры, очистка и свободное место."""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core import storage
from penguincraft.core.instances import InstanceManager
from penguincraft.core.paths import Paths
from penguincraft.core.settings import Settings


def fill(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def make_version(paths: Paths, version_id: str, *, inherits: str = "") -> None:
    data = {"id": version_id}
    if inherits:
        data["inheritsFrom"] = inherits
    target = paths.version_json(version_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")


class StorageScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = Paths(self._tmp.name)
        self.paths.ensure_base_dirs()
        self.instances = InstanceManager(self.paths, Settings())

    def test_counts_categories_and_total(self) -> None:
        fill(self.paths.versions / "1.20.1" / "client.jar", 1000)
        fill(self.paths.libraries / "a" / "b.jar", 500)
        fill(self.paths.cache / "downloads" / "x.part", 300)
        fill(self.paths.cache / "import" / "tmp.bin", 200)
        fill(self.paths.logs / "launcher.log", 100)
        self.instances.create("Сборка", version_id="1.20.1")

        report = storage.scan(self.paths, self.instances.list())
        sizes = {entry.key: entry.size for entry in report.entries}

        self.assertGreaterEqual(sizes["versions"], 1000)
        self.assertEqual(sizes["libraries"], 500)
        self.assertEqual(sizes["cache-downloads"], 300)
        self.assertEqual(sizes["cache-temp"], 200)
        self.assertEqual(sizes["logs"], 100)
        self.assertGreater(sizes["instances"], 0)
        self.assertGreaterEqual(report.total, 2100 + sizes["instances"])
        self.assertEqual(len(report.instances), 1)

    def test_unused_versions_follow_inherits(self) -> None:
        make_version(self.paths, "1.20.1")
        make_version(self.paths, "fabric-loader-0.15-1.20.1", inherits="1.20.1")
        make_version(self.paths, "1.19.4")
        self.instances.create(
            "Сборка",
            version_id="fabric-loader-0.15-1.20.1",
        )

        report = storage.scan(self.paths, self.instances.list())
        self.assertEqual(report.unused_versions, ["1.19.4"])

    def test_no_instances_means_no_cleanup(self) -> None:
        make_version(self.paths, "1.20.1")
        report = storage.scan(self.paths, [])
        self.assertEqual(report.unused_versions, [])

    def test_unused_runtimes(self) -> None:
        data = {"id": "1.20.1", "javaVersion": {"majorVersion": 17}}
        target = self.paths.version_json("1.20.1")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        fill(self.paths.runtimes / "jre-17" / "bin" / "java", 100)
        fill(self.paths.runtimes / "jre-21" / "bin" / "java", 200)
        instance = self.instances.create("Сборка", version_id="1.20.1")

        report = storage.scan(self.paths, [instance])
        self.assertEqual(report.unused_runtimes, ["jre-21"])

        freed = storage.clean_unused_runtimes(self.paths, report.unused_runtimes)
        self.assertGreaterEqual(freed, 200)
        self.assertTrue((self.paths.runtimes / "jre-17").is_dir())
        self.assertFalse((self.paths.runtimes / "jre-21").exists())

    def test_runtime_used_by_java_path_is_kept(self) -> None:
        target = self.paths.version_json("1.20.1")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"id": "1.20.1"}), encoding="utf-8")
        fill(self.paths.runtimes / "jre-21" / "bin" / "java", 200)
        instance = self.instances.create("Сборка", version_id="1.20.1")
        instance.java_path = str(
            self.paths.runtimes / "jre-21" / "bin" / "java"
        )

        report = storage.scan(self.paths, [instance])
        self.assertEqual(report.unused_runtimes, [])

    def test_no_known_java_means_no_cleanup(self) -> None:
        fill(self.paths.runtimes / "jre-21" / "bin" / "java", 200)
        self.instances.create("Сборка", version_id="1.20.1")
        report = storage.scan(self.paths, self.instances.list())
        self.assertEqual(report.unused_runtimes, [])

    def test_free_space_reports_something(self) -> None:
        free, total = storage.free_space(self.paths.root)
        self.assertGreater(free, 0)
        self.assertGreaterEqual(total, free)


class StorageCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = Paths(self._tmp.name)
        self.paths.ensure_base_dirs()
        self.instances = InstanceManager(self.paths, Settings())

    def test_clean_cache_removes_downloads_and_temp(self) -> None:
        fill(self.paths.downloads / "x.part", 300)
        fill(self.paths.cache / "import" / "tmp.bin", 200)
        fill(self.paths.cache / "updates" / "new.exe", 100)
        fill(self.paths.cache / "deleted-instances" / "keep.zip", 50)

        freed = storage.clean_cache(self.paths)

        self.assertEqual(freed, 600)
        self.assertFalse((self.paths.downloads / "x.part").exists())
        self.assertFalse((self.paths.cache / "import" / "tmp.bin").exists())
        self.assertFalse((self.paths.cache / "updates" / "new.exe").exists())
        self.assertTrue((self.paths.cache / "deleted-instances" / "keep.zip").exists())
        self.assertTrue(self.paths.downloads.is_dir())

    def test_clean_logs_only_old_files(self) -> None:
        old = fill(self.paths.logs / "old.log", 100)
        fresh = fill(self.paths.logs / "fresh.log", 100)
        stamp = time.time() - 30 * 86400
        os.utime(old, (stamp, stamp))

        freed, removed = storage.clean_logs(self.paths, days=7)

        self.assertEqual((freed, removed), (100, 1))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_clean_unused_versions(self) -> None:
        make_version(self.paths, "1.20.1")
        make_version(self.paths, "1.19.4")
        fill(self.paths.version_dir("1.19.4") / "client.jar", 400)
        report = storage.scan(self.paths, [])
        self.assertEqual(report.unused_versions, [])

        self.instances.create("Сборка", version_id="1.20.1")
        report = storage.scan(self.paths, self.instances.list())
        self.assertEqual(report.unused_versions, ["1.19.4"])
        freed = storage.clean_unused_versions(self.paths, report.unused_versions)

        self.assertGreaterEqual(freed, 400)
        self.assertFalse(self.paths.version_dir("1.19.4").exists())
        self.assertTrue(self.paths.version_dir("1.20.1").exists())

    def test_clean_deleted_backups(self) -> None:
        fill(self.paths.cache / "deleted-instances" / "mods" / "a.jar", 321)
        freed = storage.clean_deleted_backups(self.paths)
        self.assertEqual(freed, 321)
        self.assertFalse((self.paths.cache / "deleted-instances" / "mods").exists())


class EnsureFreeSpaceTests(unittest.TestCase):
    def test_huge_request_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(storage.NotEnoughSpaceError) as caught:
                storage.ensure_free_space(tmp, 10 ** 18, what="тест")
            self.assertGreaterEqual(caught.exception.needed, 10 ** 18)
            self.assertIn("тест", str(caught.exception))
            self.assertIn("Недостаточно места", str(caught.exception))

    def test_zero_needed_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            storage.ensure_free_space(tmp, 0)

    def test_format_size(self) -> None:
        self.assertEqual(storage.format_size(0), "0 Б")
        self.assertIn("КБ", storage.format_size(2048))
        self.assertIn("МБ", storage.format_size(5 * 1024 * 1024))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
