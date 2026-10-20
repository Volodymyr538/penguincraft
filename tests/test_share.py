"""Тесты списков модов и сравнения экземпляров."""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.manager import LauncherManager
from penguincraft.mods import share as mod_share


def make_mod(path: Path, *, mod_id: str, version: str = "1.0") -> Path:
    metadata = {
        "schemaVersion": 1,
        "id": mod_id,
        "name": mod_id.title(),
        "version": version,
        "description": "test",
        "authors": ["tester"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps(metadata))
    return path


def make_mods(manager: LauncherManager, instance, items) -> None:
    directory = manager.instances.mods_dir(instance.name)
    for mod_id, version, disabled in items:
        name = f"{mod_id}-{version}.jar"
        if disabled:
            name += ".disabled"
        make_mod(directory / name, mod_id=mod_id, version=version)


class ModShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = LauncherManager(self._tmp.name)
        self.left = self.manager.instances.create(
            "Слева", version_id="1.20.1", loader="fabric"
        )
        self.right = self.manager.instances.create(
            "Справа", version_id="1.20.1", loader="fabric"
        )
        make_mods(
            self.manager,
            self.left,
            [("sodium", "1.0", False), ("lithium", "1.0", False)],
        )
        make_mods(
            self.manager,
            self.right,
            [("sodium", "2.0", False), ("ferrite", "1.0", True)],
        )

    def test_collect_counts_and_status(self) -> None:
        entries = mod_share.collect(self.manager, self.left)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry.enabled for entry in entries))

        right = mod_share.collect(self.manager, self.right)
        disabled = [entry for entry in right if not entry.enabled]
        self.assertEqual(len(disabled), 1)
        self.assertEqual(disabled[0].name, "Ferrite")

    def test_export_markdown(self) -> None:
        target = Path(self._tmp.name) / "mods.md"
        path = self.manager.export_mod_list(self.left, target, fmt="md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("# Моды — Слева", text)
        self.assertIn("Sodium", text)
        self.assertIn("Minecraft: 1.20.1", text)

    def test_export_json(self) -> None:
        target = Path(self._tmp.name) / "mods.json"
        path = self.manager.export_mod_list(self.left, target, fmt="json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], mod_share.SCHEMA)
        self.assertEqual(data["instance"]["name"], self.left.name)
        self.assertEqual({item["mod_id"] for item in data["mods"]}, {"sodium", "lithium"})

    def test_compare(self) -> None:
        result = self.manager.compare_instances(self.left, self.right)
        self.assertEqual([item.name for item in result.only_left], ["Lithium"])
        self.assertEqual([item.name for item in result.only_right], ["Ferrite"])
        self.assertEqual(len(result.differing), 1)
        left, right = result.differing[0]
        self.assertEqual(left.name, "Sodium")
        self.assertEqual((left.version, right.version), ("1.0", "2.0"))
        self.assertFalse(result.identical)

    def test_compare_same(self) -> None:
        result = self.manager.compare_instances(self.left, self.left)
        self.assertTrue(result.identical)
        self.assertEqual(len(result.same), 2)

    def test_render_comparison(self) -> None:
        text = mod_share.render_comparison(
            self.manager.compare_instances(self.left, self.right)
        )
        self.assertIn("Только в «Слева»", text)
        self.assertIn("Только в «Справа»", text)
        self.assertIn("Разные версии", text)


class EmptyInstanceTests(unittest.TestCase):
    def test_empty_instance_markdown(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = LauncherManager(tmp)
            instance = manager.instances.create("Пусто", version_id="1.20.1")
            text = mod_share.render_markdown(instance, [])
            self.assertIn("_Модов нет._", text)

    def test_no_comparison_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = LauncherManager(tmp)
            one = manager.instances.create("Один", version_id="1.20.1")
            two = manager.instances.create("Два", version_id="1.20.1")
            result = manager.compare_instances(one, two)
            self.assertTrue(result.identical)
            self.assertIn("совпадают", mod_share.render_comparison(result))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
