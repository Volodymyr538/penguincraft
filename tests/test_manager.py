"""Тесты фасада ``LauncherManager`` (без сети и графики)."""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.manager import LauncherManager
from penguincraft.mods import manager as mod_manager


def make_mod(path: Path, *, mod_id: str, depends=None, loader="fabric") -> Path:
    metadata = {
        "schemaVersion": 1,
        "id": mod_id,
        "name": mod_id.title(),
        "version": "1.0",
        "description": "test",
        "authors": ["tester"],
        "depends": depends or {},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps(metadata))
    return path


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = LauncherManager(self._tmp.name)

    def test_construction_creates_layout(self) -> None:
        self.assertTrue(self.manager.paths.root.is_dir())
        self.assertEqual(self.manager.instances.list(), [])

    def test_settings_saved_and_reloaded(self) -> None:
        self.manager.settings.auto_update_mods = True
        self.manager.settings.ui_scale = 1.5
        self.manager.save_settings()
        other = LauncherManager(self._tmp.name)
        self.assertTrue(other.settings.auto_update_mods)
        self.assertAlmostEqual(other.settings.ui_scale, 1.5)

    def test_create_instance_and_health(self) -> None:
        instance = self.manager.instances.create("Сборка", version_id="1.20.1")
        mods_dir = self.manager.instances.mods_dir(instance.name)
        make_mod(mods_dir / "a.jar", mod_id="coolmod", depends={"fabric-api": "*"})

        health = self.manager.check_instance_health(instance)
        self.assertIn("missing", health)
        self.assertTrue(any("fabric-api" in item for item in health["missing"]))

    def test_health_reports_disabled_dependency(self) -> None:
        instance = self.manager.instances.create("Сборка", version_id="1.20.1")
        mods_dir = self.manager.instances.mods_dir(instance.name)
        make_mod(mods_dir / "a.jar", mod_id="coolmod", depends={"helper": "*"})
        make_mod(mods_dir / "helper.jar", mod_id="helper")
        mod_manager.set_enabled(mod_manager.list_mods(mods_dir)[1], False)

        health = self.manager.check_instance_health(instance)
        self.assertIn("disabled", health)
        self.assertIn("missing", health)

    def test_health_reports_wrong_loader(self) -> None:
        instance = self.manager.instances.create("Forge-сборка", version_id="1.20.1")
        instance.loader = "forge"
        self.manager.instances.save(instance)
        mods_dir = self.manager.instances.mods_dir(instance.name)
        make_mod(mods_dir / "a.jar", mod_id="fabric-mod", loader="fabric")

        health = self.manager.check_instance_health(instance)
        self.assertIn("loaders", health)

    def test_health_ok_for_vanilla_loaders(self) -> None:
        instance = self.manager.instances.create("Ваниль", version_id="1.20.1")
        mods_dir = self.manager.instances.mods_dir(instance.name)
        make_mod(mods_dir / "a.jar", mod_id="coolmod", depends={"minecraft": "*"})
        self.assertEqual(self.manager.check_instance_health(instance), {})

    def test_worlds_and_export(self) -> None:
        instance = self.manager.instances.create("Сборка", version_id="1.20.1")
        world = self.manager.instances.saves_dir(instance.name) / "Мир"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(b"level")
        self.assertEqual(len(self.manager.worlds(instance)), 1)
        target = Path(self._tmp.name) / "w.zip"
        self.manager.export_world(instance, "Мир", target)
        self.assertTrue(target.is_file())

    def test_auto_update_respects_interval(self) -> None:
        import time

        self.manager.settings.auto_update_hours = 24
        self.manager.settings.last_mod_update_check = time.time()
        self.manager.save_settings()
        counters = self.manager.auto_update_mods()
        self.assertEqual(counters, {"instances": 0, "updated": 0, "failed": 0, "checked": 0})

    def test_auto_update_no_instances(self) -> None:
        counters = self.manager.auto_update_mods(force=True)
        self.assertEqual(counters["instances"], 0)
        self.assertTrue(self.manager.settings.last_mod_update_check > 0)

    def test_installed_index_per_instance(self) -> None:
        first = self.manager.instances.create("A", version_id="1.20.1")
        second = self.manager.instances.create("B", version_id="1.20.1")
        (self.manager.instances.mods_dir(first.name) / "x.jar").write_bytes(b"jar")
        index = self.manager.installed_index(first)
        index.record("x.jar", project_id="proj")
        index.save()
        self.assertEqual(len(self.manager.installed_index(first).items()), 1)
        self.assertEqual(self.manager.installed_index(second).items(), [])

    def test_verify_requires_version(self) -> None:
        from penguincraft.manager import LauncherError

        instance = self.manager.instances.create("Пустой", version_id="")
        with self.assertRaises(LauncherError):
            self.manager.verify_instance(instance)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
