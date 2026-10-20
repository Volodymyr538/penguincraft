"""Тесты экспорта диагностики."""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core import diagnostics
from penguincraft.manager import LauncherManager


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = LauncherManager(self._tmp.name)
        self.manager.add_offline_account("Tester")
        self.manager.settings.proxy = "http://user:secret@10.0.0.1:8080"
        self.manager.save_settings()
        self.instance = self.manager.instances.create(
            "Сборка", version_id="1.20.1", loader="fabric"
        )
        self.manager.paths.logs.mkdir(parents=True, exist_ok=True)
        (self.manager.paths.logs / "launcher.log").write_text(
            "первая строка\nвторая строка\n", encoding="utf-8"
        )

    def test_report_has_no_secrets(self) -> None:
        report = diagnostics.build_report(self.manager)
        dumped = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("secret", dumped)
        self.assertEqual(report["accounts"]["count"], 1)
        self.assertEqual(report["settings"]["proxy"], "http://user:***@10.0.0.1:8080")
        self.assertEqual(report["instances"][0]["name"], self.instance.name)
        self.assertIn("generated_at", report)

    def test_render_text_is_russian(self) -> None:
        text = diagnostics.render_text(diagnostics.build_report(self.manager))
        self.assertIn("отчёт диагностики", text)
        self.assertIn("Система", text)
        self.assertIn("Экземпляры", text)
        self.assertIn("Сборка", text)

    def test_export_zip_contents(self) -> None:
        target = Path(self._tmp.name) / "diag.zip"
        path = self.manager.export_diagnostics(target)

        self.assertEqual(path, target)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            self.assertIn("report.txt", names)
            self.assertIn("report.json", names)
            self.assertIn("logs/launcher.log", names)
            self.assertNotIn("accounts.json", names)
            self.assertNotIn("settings.json", names)
            text = archive.read("report.txt").decode("utf-8")
            self.assertIn("PenguinCraft", text)
            payload = json.loads(archive.read("report.json").decode("utf-8"))
            self.assertEqual(payload["settings"]["proxy"], "http://user:***@10.0.0.1:8080")

    def test_logs_and_crash_reports_keep_paths(self) -> None:
        nested = self.manager.paths.logs / "2026-10-20"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "game.log").write_text("лог игры\n", encoding="utf-8")
        crashes = (
            self.manager.instances.game_dir(self.instance.name) / "crash-reports"
        )
        crashes.mkdir(parents=True, exist_ok=True)
        (crashes / "crash-2026-10-20.txt").write_text(
            "отчёт о падении\n", encoding="utf-8"
        )

        target = Path(self._tmp.name) / "diag2.zip"
        self.manager.export_diagnostics(target)

        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertIn("logs/2026-10-20/game.log", names)
            self.assertIn(
                f"crash-reports/{self.instance.name}-1-crash-2026-10-20.txt", names
            )

    def test_default_name(self) -> None:
        name = diagnostics.default_name()
        self.assertTrue(name.startswith("penguincraft-diagnostics-"))
        self.assertTrue(name.endswith(".zip"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
