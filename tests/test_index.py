"""Тесты индекса установленных проектов (``installed.json``)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.mods.index import InstalledIndex, InstalledProject


class InstalledProjectTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        record = InstalledProject(filename="a.jar", project_id="abc", requires=["x"])
        restored = InstalledProject.from_dict(record.to_dict())
        self.assertEqual(restored.project_id, "abc")
        self.assertEqual(restored.requires, ["x"])

    def test_coerces_lists_and_drops_bad(self) -> None:
        record = InstalledProject.from_dict(
            {"filename": "a.jar", "requires": ["x", 1], "incompatible": "bad", "files": None}
        )
        self.assertEqual(record.requires, ["x", "1"])
        self.assertEqual(record.incompatible, [])

    def test_unknown_keys_ignored(self) -> None:
        record = InstalledProject.from_dict({"filename": "a.jar", "nope": 1})
        self.assertEqual(record.filename, "a.jar")

    def test_bad_types_fall_back(self) -> None:
        record = InstalledProject.from_dict({"installed_at": "not-a-number"})
        self.assertEqual(record.filename, "")


class InstalledIndexTests(unittest.TestCase):
    def test_record_and_get(self) -> None:
        index = InstalledIndex("/nonexistent/installed.json")
        index.record("sodium.jar", project_id="AANobbMI", version_id="v1")
        self.assertEqual(index.get("sodium.jar").project_id, "AANobbMI")
        self.assertEqual(len(index.items()), 1)

    def test_forget(self) -> None:
        index = InstalledIndex("/tmp/none.json")
        index.record("a.jar")
        index.record("b.jar")
        self.assertEqual(index.forget(["a.jar", "missing.jar"]), 1)
        self.assertEqual(index.forget([]), 0)
        self.assertIsNone(index.get("a.jar"))

    def test_prune(self) -> None:
        index = InstalledIndex("/tmp/none.json")
        index.record("a.jar")
        index.record("b.jar")
        index.record("c.jar")
        self.assertEqual(index.prune(["a.jar", "c.jar"]), 1)
        self.assertEqual(sorted(index.files), ["a.jar", "c.jar"])

    def test_pack(self) -> None:
        index = InstalledIndex("/tmp/none.json")
        index.set_pack(project_id="pack", title="Сборка", files=["a.jar"])
        self.assertEqual(index.pack.project_id, "pack")

    def test_save_and_load(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed.json"
            index = InstalledIndex(path)
            index.record("sodium.jar", project_id="AANobbMI", version_id="v1", title="Sodium")
            index.set_pack(project_id="pack", title="Сборка", files=["sodium.jar"])
            index.save()

            loaded = InstalledIndex(path).load()
            self.assertEqual(loaded.get("sodium.jar").title, "Sodium")
            self.assertEqual(loaded.pack.title, "Сборка")
            self.assertEqual(loaded.pack.files, ["sodium.jar"])

    def test_load_missing_file_is_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            index = InstalledIndex(Path(tmp) / "absent.json").load()
            self.assertEqual(index.items(), [])
            self.assertIsNone(index.pack)

    def test_load_broken_file_is_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed.json"
            path.write_text("oops", encoding="utf-8")
            index = InstalledIndex(path).load()
            self.assertEqual(index.items(), [])

    def test_filename_defaults_to_key(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "installed.json"
            path.write_text(
                '{"files": {"a.jar": {"project_id": "x"}}}', encoding="utf-8"
            )
            index = InstalledIndex(path).load()
            self.assertEqual(index.get("a.jar").filename, "a.jar")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
