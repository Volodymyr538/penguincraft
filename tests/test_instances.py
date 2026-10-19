"""Тесты экземпляров: создание, миры, экспорт/импорт."""

from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core.instances import InstanceError, InstanceManager, _world_root
from penguincraft.core.paths import Paths
from penguincraft.core.settings import Settings


def make_manager(tmp: str) -> InstanceManager:
    paths = Paths(tmp)
    paths.ensure_base_dirs()
    return InstanceManager(paths, Settings())


def make_world(root: Path, name: str, *, level: bool = True, marker: str = "x") -> Path:
    world = root / name
    (world / "region").mkdir(parents=True, exist_ok=True)
    if level:
        (world / "level.dat").write_bytes(b"level" + marker.encode())
    (world / "region" / "r.0.0.mca").write_bytes(b"region" * 10)
    (world / "icon.png").write_bytes(b"\x89PNG")
    return world


class CreateTests(unittest.TestCase):
    def test_create_dirs_and_config(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            instance = manager.create("Моя сборка", version_id="1.20.1")
            self.assertEqual(instance.display_name, "Моя сборка")
            self.assertTrue(manager.game_dir(instance.name).is_dir())
            for sub in ("mods", "config", "resourcepacks", "shaderpacks", "saves"):
                self.assertTrue((manager.game_dir(instance.name) / sub).is_dir())
            self.assertTrue(manager.paths.instance_json(instance.name).is_file())

    def test_unique_name(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            first = manager.create("Сборка", version_id="1.20.1")
            second = manager.create("Сборка", version_id="1.20.1")
            self.assertNotEqual(first.name, second.name)
            self.assertTrue(second.name.startswith("Сборка"))

    def test_clone_and_rename(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            clone = manager.clone("A", "B")
            self.assertEqual(clone.display_name, "B")
            renamed = manager.rename(clone.name, "C")
            self.assertEqual(renamed.display_name, "C")

    def test_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            manager.delete("A")
            self.assertFalse(manager.exists("A"))
            with self.assertRaises(InstanceError):
                manager.delete("A")

    def test_project_dir_by_type(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            self.assertEqual(manager.project_dir("A", "mod").name, "mods")
            self.assertEqual(manager.project_dir("A", "resourcepack").name, "resourcepacks")
            self.assertEqual(manager.project_dir("A", "shader").name, "shaderpacks")
            self.assertEqual(manager.project_dir("A", "mods").name, "mods")


class WorldTests(unittest.TestCase):
    def test_world_infos(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            make_world(manager.saves_dir("A"), "Мир 1")
            infos = manager.world_infos("A")
            self.assertEqual(len(infos), 1)
            info = infos[0]
            self.assertEqual(info.name, "Мир 1")
            self.assertTrue(info.has_level)
            self.assertTrue(info.has_icon)
            self.assertGreater(info.size, 0)
            self.assertNotEqual(info.modified_text, "—")

    def test_export_and_import_world(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            make_world(manager.saves_dir("A"), "Мир")
            target = Path(tmp) / "out" / "world.zip"
            manager.export_world("A", "Мир", target)
            self.assertTrue(target.is_file())

            manager.create("B", version_id="1.20.1")
            imported = manager.import_world("B", target)
            self.assertTrue((manager.saves_dir("B") / imported.name / "level.dat").is_file())

    def test_import_world_with_wrapper_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_world(root / "stage", "MyWorld")
            archive = root / "wrapped.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for path in source.rglob("*"):
                    if path.is_file():
                        zf.write(path, path.relative_to(source.parent).as_posix())
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            imported = manager.import_world("A", archive, world_name="Копия")
            self.assertEqual(imported.name, "Копия")
            self.assertTrue((imported.path / "level.dat").is_file())

    def test_import_rejects_non_world(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("readme.txt", "not a world")
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            with self.assertRaises(InstanceError):
                manager.import_world("A", archive)

    def test_delete_world(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            make_world(manager.saves_dir("A"), "Мир")
            manager.delete_world("A", "Мир")
            self.assertEqual(manager.worlds("A"), [])
            with self.assertRaises(InstanceError):
                manager.delete_world("A", "Мир")

    def test_unique_world_name(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            make_world(manager.saves_dir("A"), "Мир")
            self.assertEqual(manager.unique_world_name("A", "Мир"), "Мир (2)")

    def test_backup_worlds(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            make_world(manager.saves_dir("A"), "Мир")
            target = Path(tmp) / "saves.zip"
            manager.backup_worlds("A", target)
            with zipfile.ZipFile(target) as zf:
                names = zf.namelist()
            self.assertTrue(any(name.startswith("saves/Мир/") for name in names))

    def test_backup_worlds_without_worlds(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            with self.assertRaises(InstanceError):
                manager.backup_worlds("A", Path(tmp) / "saves.zip")

    def test_world_root_helper(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = make_world(root / "direct", "W")
            self.assertEqual(_world_root(direct), direct)
            wrapper = root / "wrap"
            wrapper.mkdir()
            self.assertIsNone(_world_root(wrapper))
            nested = make_world(wrapper, "Inner")
            self.assertEqual(_world_root(wrapper), nested)


class ExportImportInstanceTests(unittest.TestCase):
    def test_export_and_import(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            source = manager.create("A", version_id="1.20.1")
            (manager.mods_dir("A") / "x.jar").write_bytes(b"jar")
            make_world(manager.saves_dir("A"), "Мир")
            archive = Path(tmp) / "instance.zip"
            manager.export_archive("A", archive)
            self.assertTrue(archive.is_file())

            imported = manager.import_archive(archive, display_name="Импорт")
            self.assertEqual(imported.display_name, "Импорт")
            self.assertTrue((manager.mods_dir(imported.name) / "x.jar").is_file())
            self.assertTrue((manager.saves_dir(imported.name) / "Мир" / "level.dat").is_file())

    def test_import_not_an_instance(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("hello.txt", "x")
            manager = make_manager(tmp)
            with self.assertRaises(InstanceError):
                manager.import_archive(archive)

    def test_disk_usage_and_export_list(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = make_manager(tmp)
            manager.create("A", version_id="1.20.1")
            (manager.mods_dir("A") / "x.jar").write_bytes(b"12345")
            self.assertGreaterEqual(manager.disk_usage("A"), 5)
            listing = manager.export_list()
            self.assertEqual(len(listing), 1)
            self.assertEqual(listing[0]["mods_count"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
