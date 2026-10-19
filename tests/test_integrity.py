"""Тесты проверки целостности файлов версии."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core.integrity import check_version
from penguincraft.core.paths import Paths
from penguincraft.core.settings import Settings


PLATFORM = {"name": "linux", "version": "6.1", "arch": "x64", "features": {}}


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class CheckVersionTests(unittest.TestCase):
    def _paths(self, tmp: str) -> Paths:
        paths = Paths(tmp)
        paths.ensure_base_dirs()
        return paths

    def _build_version(self, paths: Paths):
        client_data = b"client-jar-bytes"
        client_path = paths.version_dir("1.20.1") / "1.20.1.jar"
        client_path.parent.mkdir(parents=True, exist_ok=True)
        client_path.write_bytes(client_data)

        lib_ok_data = b"library-ok"
        lib_ok = paths.libraries / "g/a/1/a-1.jar"
        lib_ok.parent.mkdir(parents=True, exist_ok=True)
        lib_ok.write_bytes(lib_ok_data)

        object_data = b"asset-object"
        object_hash = sha1(object_data)
        object_path = paths.asset_objects / object_hash[:2] / object_hash
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(object_data)

        index = {
            "objects": {
                "minecraft/sounds/a.ogg": {"hash": object_hash, "size": len(object_data)},
                "minecraft/sounds/missing.ogg": {"hash": "b" * 40, "size": 12},
            }
        }
        (paths.asset_indexes / "5.json").write_text(json.dumps(index), encoding="utf-8")

        version = {
            "id": "1.20.1",
            "downloads": {
                "client": {
                    "url": "https://example/client.jar",
                    "sha1": sha1(client_data),
                    "size": len(client_data),
                }
            },
            "libraries": [
                {
                    "name": "g:a:1",
                    "downloads": {
                        "artifact": {
                            "url": "https://example/a-1.jar",
                            "path": "g/a/1/a-1.jar",
                            "sha1": sha1(lib_ok_data),
                            "size": len(lib_ok_data),
                        }
                    },
                },
                {
                    "name": "g:missing:1",
                    "downloads": {
                        "artifact": {
                            "url": "https://example/missing-1.jar",
                            "path": "g/missing/1/missing-1.jar",
                            "sha1": "c" * 40,
                            "size": 5,
                        }
                    },
                },
            ],
            "assetIndex": {"id": "5", "url": "https://example/5.json", "size": 10},
        }
        return version

    def test_detects_missing_files(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            version = self._build_version(paths)
            report = check_version(paths, version, "1.20.1", platform=PLATFORM)
            kinds = sorted(issue.reason for issue in report.issues)
            self.assertEqual(kinds.count("missing"), 2)  # библиотека + ресурс
            self.assertFalse(report.ok)
            self.assertEqual(report.counts().get("library"), 1)
            self.assertEqual(report.counts().get("asset"), 1)

    def test_size_mismatch(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            version = self._build_version(paths)
            # портим размер клиента
            client = paths.version_dir("1.20.1") / "1.20.1.jar"
            client.write_bytes(b"x")
            report = check_version(paths, version, "1.20.1", platform=PLATFORM)
            client_issues = [i for i in report.issues if i.kind == "client"]
            self.assertEqual(len(client_issues), 1)
            self.assertEqual(client_issues[0].reason, "size")

    def test_deep_hash_detection(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            version = self._build_version(paths)
            # тот же размер, другой хеш
            lib = paths.libraries / "g/a/1/a-1.jar"
            lib.write_bytes(b"library-BAD")  # len 11 == len("library-ok")? 10 vs 11
            lib.write_bytes(b"library-XY")
            report = check_version(paths, version, "1.20.1", deep=True, platform=PLATFORM)
            lib_issues = [i for i in report.issues if i.kind == "library"]
            self.assertTrue(any(issue.reason in ("size", "hash") for issue in lib_issues))

    def test_clean_installation(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            version = self._build_version(paths)
            # добавляем недостающие файлы
            missing_lib = paths.libraries / "g/missing/1/missing-1.jar"
            missing_lib.parent.mkdir(parents=True, exist_ok=True)
            missing_lib.write_bytes(b"12345")
            object_data = b"asset-object-2"
            object_hash = sha1(object_data)
            target = paths.asset_objects / object_hash[:2] / object_hash
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(object_data)
            index_path = paths.asset_indexes / "5.json"
            index = json.loads(index_path.read_text())
            index["objects"] = {
                "minecraft/sounds/a.ogg": index["objects"]["minecraft/sounds/a.ogg"],
                "minecraft/sounds/missing.ogg": {"hash": object_hash, "size": len(object_data)},
            }
            index_path.write_text(json.dumps(index), encoding="utf-8")

            report = check_version(paths, version, "1.20.1", platform=PLATFORM)
            self.assertTrue(report.ok, report.issues)
            self.assertGreater(report.checked, 3)

    def test_missing_asset_index_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            version = self._build_version(paths)
            (paths.asset_indexes / "5.json").unlink()
            report = check_version(paths, version, "1.20.1", platform=PLATFORM)
            self.assertTrue(any(issue.kind == "index" for issue in report.issues))

    def test_summary_text(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            version = self._build_version(paths)
            report = check_version(paths, version, "1.20.1", platform=PLATFORM)
            self.assertIn("Проверено файлов", report.summary())


class RepairVersionTests(unittest.TestCase):
    """Проверяет логику восстановления при заглушке установщика (без сети)."""

    def test_repair_redownloads_missing(self) -> None:
        from penguincraft.core import integrity as integrity_module

        with TemporaryDirectory() as tmp:
            paths = Paths(tmp)
            paths.ensure_base_dirs()
            version = CheckVersionTests._build_version(self, paths)

            # ломаем библиотеку и удаляем файл ресурса
            lib_path = paths.libraries / "g/a/1/a-1.jar"
            lib_path.write_bytes(b"corrupted")
            index = json.loads((paths.asset_indexes / "5.json").read_text())
            broken_hash = index["objects"]["minecraft/sounds/a.ogg"]["hash"]
            (paths.asset_objects / broken_hash[:2] / broken_hash).unlink()

            before = check_version(paths, version, "1.20.1", platform=PLATFORM)
            self.assertFalse(before.ok)

            class FakeRepo:
                def resolve(self, version_id, reporter=None):
                    return version

            class PlanResult:
                version = None

            calls = []
            plan = PlanResult()
            plan.version = version

            def fake_install(*args, **kwargs):
                calls.append(1)
                lib_path.parent.mkdir(parents=True, exist_ok=True)
                lib_path.write_bytes(b"library-ok")
                missing_lib = paths.libraries / "g/missing/1/missing-1.jar"
                missing_lib.parent.mkdir(parents=True, exist_ok=True)
                missing_lib.write_bytes(b"12345")
                target = paths.asset_objects / broken_hash[:2] / broken_hash
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"asset-object")
                # второй объект из индекса (тоже отсутствовал)
                second = paths.asset_objects / "bb" / ("b" * 40)
                second.parent.mkdir(parents=True, exist_ok=True)
                second.write_bytes(b"twelve bytes")
                return plan

            original = integrity_module.install_version
            integrity_module.install_version = fake_install
            try:
                report = integrity_module.repair_version(
                    paths,
                    Settings(),
                    "1.20.1",
                    FakeHttpStub(),
                    repository=FakeRepo(),
                )
            finally:
                integrity_module.install_version = original

            self.assertEqual(len(calls), 1)
            self.assertTrue(report.ok, report.issues)
            self.assertGreaterEqual(report.repaired, 1)

    def test_repair_skipped_when_healthy(self) -> None:
        from penguincraft.core import integrity as integrity_module

        with TemporaryDirectory() as tmp:
            paths = Paths(tmp)
            paths.ensure_base_dirs()
            jar = paths.version_dir("clean") / "clean.jar"
            jar.parent.mkdir(parents=True, exist_ok=True)
            jar.write_bytes(b"already fine")
            version = {"id": "clean", "libraries": []}

            class FakeRepo:
                def resolve(self, version_id, reporter=None):
                    return version

            def fail_install(*args, **kwargs):
                raise AssertionError("установщик не должен вызываться")

            original = integrity_module.install_version
            integrity_module.install_version = fail_install
            try:
                report = integrity_module.repair_version(
                    paths,
                    Settings(),
                    "clean",
                    FakeHttpStub(),
                    repository=FakeRepo(),
                )
            finally:
                integrity_module.install_version = original

            self.assertTrue(report.ok)
            self.assertEqual(report.repaired, 0)


class FakeHttpStub:
    def get_json(self, *args, **kwargs):  # pragma: no cover - не используется
        raise AssertionError

    def download(self, *args, **kwargs):  # pragma: no cover - не используется
        raise AssertionError


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
