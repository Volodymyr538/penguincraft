"""Тесты разбора метаданных модов и операций с каталогом ``mods``."""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.mods import manager as mod_manager

FABRIC = {
    "schemaVersion": 1,
    "id": "sodium",
    "name": "Sodium",
    "version": "0.5.3",
    "description": "Быстрый рендер",
    "authors": ["jellysquid3"],
    "depends": {"minecraft": ">=1.20", "fabricloader": ">=0.14", "fabric-api": "*"},
}

QUILT = {
    "schema_version": 1,
    "quilt_loader": {
        "id": "example",
        "version": "1.0",
        "metadata": {"name": "Example", "description": "demo", "contributors": {"a": "A"}},
        "depends": [{"id": "quilt_base"}, {"id": "minecraft", "versions": ">=1.20"}],
    },
}

FORGE_TOML = """
modLoader="javafml"
mods=[{ modId="jei", displayName="JEI", version="15.0", description="Items", authors="me" }]

[[dependencies.jei]]
modId="forge"
mandatory=true
versionRange="[47,)"

[[dependencies.jei]]
modId="minecraft"
mandatory=true

[[dependencies.jei]]
modId="optional-one"
mandatory=false
"""

FORGE_TOML_TABLE = """
modLoader="javafml"

[[mods]]
modId="coolmod"
displayName="Cool Mod"
version="2.1"
description="Cool"
authors=["Alice", "Bob"]

[[dependencies.coolmod]]
modId="minecraft"
mandatory=true
versionRange="[1.20,1.21)"
"""

MCMOD = [
    {
        "modid": "oldmod",
        "name": "Old Mod",
        "version": "2.0",
        "description": "старый",
        "authorList": ["author"],
    }
]


def make_jar(path: Path, entries) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


class ParseMetadataTests(unittest.TestCase):
    def test_fabric(self) -> None:
        with TemporaryDirectory() as tmp:
            jar = make_jar(
                Path(tmp) / "sodium.jar",
                [("fabric.mod.json", json.dumps(FABRIC))],
            )
            meta = mod_manager.parse_mod_metadata(jar)
            self.assertEqual(meta["id"], "sodium")
            self.assertEqual(meta["name"], "Sodium")
            self.assertEqual(meta["loader"], "fabric")
            self.assertIn("fabric-api", meta["depends"])
            self.assertIn("minecraft", meta["depends"])

    def test_quilt(self) -> None:
        with TemporaryDirectory() as tmp:
            jar = make_jar(
                Path(tmp) / "example.jar",
                [("quilt.mod.json", json.dumps(QUILT))],
            )
            meta = mod_manager.parse_mod_metadata(jar)
            self.assertEqual(meta["loader"], "quilt")
            self.assertEqual(meta["id"], "example")
            self.assertIn("quilt_base", meta["depends"])

    def test_forge_toml(self) -> None:
        with TemporaryDirectory() as tmp:
            jar = make_jar(
                Path(tmp) / "jei.jar",
                [("META-INF/mods.toml", FORGE_TOML)],
            )
            meta = mod_manager.parse_mod_metadata(jar)
            self.assertEqual(meta["id"], "jei")
            self.assertEqual(meta["loader"], "forge")
            self.assertEqual(meta["authors"], ["me"])
            # not-mandatory зависимость не должна попасть
            self.assertIn("forge", meta["depends"])
            self.assertIn("minecraft", meta["depends"])
            self.assertNotIn("optional-one", meta["depends"])

    def test_forge_toml_without_tomllib(self) -> None:
        """Проверяет резервный разбор TOML (Python 3.9/3.10 без tomllib)."""
        import sys

        saved = sys.modules.get("tomllib", "absent")
        sys.modules["tomllib"] = None  # заставляет ``import tomllib`` упасть
        try:
            with TemporaryDirectory() as tmp:
                jar = make_jar(
                    Path(tmp) / "jei.jar",
                    [("META-INF/mods.toml", FORGE_TOML)],
                )
                meta = mod_manager.parse_mod_metadata(jar)
        finally:
            if saved == "absent":
                sys.modules.pop("tomllib", None)
            else:
                sys.modules["tomllib"] = saved

        self.assertEqual(meta["id"], "jei")
        self.assertEqual(meta["name"], "JEI")
        self.assertIn("forge", meta["depends"])
        self.assertIn("minecraft", meta["depends"])
        self.assertNotIn("optional-one", meta["depends"])

    def test_forge_toml_array_of_tables(self) -> None:
        with TemporaryDirectory() as tmp:
            jar = make_jar(
                Path(tmp) / "cool.jar",
                [("META-INF/mods.toml", FORGE_TOML_TABLE)],
            )
            meta = mod_manager.parse_mod_metadata(jar)
            self.assertEqual(meta["id"], "coolmod")
            self.assertEqual(meta["name"], "Cool Mod")
            self.assertEqual(meta["authors"], ["Alice", "Bob"])
            # Диапазон версий разбирается только через tomllib, поэтому
            # проверяем лишь наличие зависимости.
            self.assertIn("minecraft", meta["depends"])

    def test_mcmod_info(self) -> None:
        with TemporaryDirectory() as tmp:
            jar = make_jar(
                Path(tmp) / "old.jar",
                [("mcmod.info", json.dumps(MCMOD))],
            )
            meta = mod_manager.parse_mod_metadata(jar)
            self.assertEqual(meta["id"], "oldmod")
            self.assertEqual(meta["loader"], "forge")

    def test_broken_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            jar = Path(tmp) / "broken.jar"
            jar.write_bytes(b"not a zip")
            meta = mod_manager.parse_mod_metadata(jar)
            self.assertTrue(meta["error"])


class ListModsTests(unittest.TestCase):
    def test_lists_only_archives(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_jar(directory / "a.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            make_jar(directory / "b.jar.disabled", [("fabric.mod.json", json.dumps(FABRIC))])
            (directory / "notes.txt").write_text("hi", encoding="utf-8")
            mods = mod_manager.list_mods(directory)
            names = [mod.filename for mod in mods]
            self.assertEqual(sorted(names), ["a.jar", "b.jar.disabled"])
            disabled = [mod for mod in mods if not mod.enabled]
            self.assertEqual(len(disabled), 1)
            self.assertEqual(disabled[0].title, "Sodium")

    def test_missing_directory(self) -> None:
        self.assertEqual(mod_manager.list_mods("/nonexistent"), [])


class ToggleTests(unittest.TestCase):
    def test_disable_enable(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_jar(directory / "a.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            mod = mod_manager.list_mods(directory)[0]
            new_path = mod_manager.set_enabled(mod, False)
            self.assertTrue(new_path.name.endswith(".disabled"))
            mod2 = mod_manager.list_mods(directory)[0]
            self.assertFalse(mod2.enabled)
            restored = mod_manager.set_enabled(mod2, True)
            self.assertEqual(restored.name, "a.jar")

    def test_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = make_jar(directory / "a.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            mod = mod_manager.list_mods(directory)[0]
            mod_manager.delete_mod(mod)
            self.assertFalse(path.exists())


class ImportTests(unittest.TestCase):
    def test_import_and_rename_on_conflict(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            jar = make_jar(source / "a.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            target = Path(tmp) / "mods"
            copied, errors = mod_manager.import_mods([jar], target)
            self.assertEqual(len(copied), 1)
            self.assertEqual(errors, [])
            copied2, errors2 = mod_manager.import_mods([jar], target)
            self.assertEqual(errors2, [])
            self.assertNotEqual(copied2[0].name, "a.jar")

    def test_import_rejects_bad_extension(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "nope.txt"
            source.write_text("x", encoding="utf-8")
            copied, errors = mod_manager.import_mods([source], Path(tmp) / "mods")
            self.assertEqual(copied, [])
            self.assertEqual(len(errors), 1)


class DuplicatesTests(unittest.TestCase):
    def test_finds_duplicates(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_jar(directory / "sodium-1.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            make_jar(directory / "sodium-2.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            groups = mod_manager.find_duplicates(directory)
            self.assertIn("sodium", groups)
            self.assertEqual(len(groups["sodium"]), 2)

    def test_no_duplicates(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_jar(directory / "a.jar", [("fabric.mod.json", json.dumps(FABRIC))])
            self.assertEqual(mod_manager.find_duplicates(directory), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
