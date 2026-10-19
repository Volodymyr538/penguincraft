"""Тесты настроек: sanitize, память, аргументы JVM, профиль экземпляра."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core.paths import Paths
from penguincraft.core.settings import (
    JVM_PRESETS,
    InstanceConfig,
    Settings,
    auto_memory_mb,
    sanitize_jvm_args,
)


class SettingsTests(unittest.TestCase):
    def test_defaults_and_sanitize(self) -> None:
        settings = Settings()
        settings.parallel_downloads = 999
        settings.window_width = 10
        settings.ui_scale = 10.0
        settings.auto_update_hours = 0
        settings.sanitize()
        self.assertEqual(settings.parallel_downloads, 32)
        self.assertEqual(settings.window_width, 880)
        self.assertEqual(settings.ui_scale, 2.0)
        self.assertEqual(settings.auto_update_hours, 24)

    def test_sanitize_clamps_bad_values(self) -> None:
        settings = Settings()
        settings.ui_scale = "abc"  # type: ignore[assignment]
        settings.auto_update_hours = None  # type: ignore[assignment]
        settings.sanitize()
        self.assertEqual(settings.ui_scale, 1.0)
        self.assertEqual(settings.auto_update_hours, 24)

    def test_from_dict_ignores_unknown_keys(self) -> None:
        settings = Settings.from_dict({"theme": "light", "nonsense": 5})
        self.assertEqual(settings.theme, "light")

    def test_save_and_load_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = Paths(tmp)
            paths.ensure_base_dirs()
            settings = Settings()
            settings.theme = "light"
            settings.auto_update_mods = True
            settings.install_dependencies = False
            settings.ui_scale = 1.25
            settings.save(paths)
            loaded = Settings.load(paths)
            self.assertEqual(loaded.theme, "light")
            self.assertTrue(loaded.auto_update_mods)
            self.assertFalse(loaded.install_dependencies)
            self.assertAlmostEqual(loaded.ui_scale, 1.25)

    def test_broken_file_loads_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = Paths(tmp)
            paths.ensure_base_dirs()
            paths.settings.write_text("{not json", encoding="utf-8")
            settings = Settings.load(paths)
            self.assertEqual(settings.theme, "dark")

    def test_memory_modes(self) -> None:
        settings = Settings()
        settings.memory_mode = "manual"
        settings.memory_mb = 4096
        self.assertEqual(settings.effective_memory_mb, 4096)
        settings.memory_mode = "auto"
        self.assertEqual(settings.effective_memory_mb, auto_memory_mb(settings.jvm_preset))

    def test_jvm_arguments_include_memory(self) -> None:
        settings = Settings()
        settings.memory_mode = "manual"
        settings.memory_mb = 2048
        args = settings.jvm_arguments(java_major=17)
        self.assertIn("-Xmx2048M", args)
        self.assertIn("-Xms2048M", args)


class AutoMemoryTests(unittest.TestCase):
    def test_reasonable_range(self) -> None:
        for preset in JVM_PRESETS:
            value = auto_memory_mb(preset)
            self.assertGreaterEqual(value, 512)
            self.assertLessEqual(value, 8192)
            self.assertEqual(value % 128, 0)


class SanitizeJvmArgsTests(unittest.TestCase):
    def test_drops_flags_for_old_java(self) -> None:
        args = ["-XX:+UseG1GC", "-XX:G1RSetUpdatingPauseTimePercent=5", "-Xmx1G"]
        result = sanitize_jvm_args(args, java_major=7)
        self.assertNotIn("-XX:G1RSetUpdatingPauseTimePercent=5", result)
        # G1 сам по себе доступен с Java 7
        self.assertIn("-XX:+UseG1GC", result)
        self.assertIn("-Xmx1G", result)

    def test_keeps_unknown_flags(self) -> None:
        result = sanitize_jvm_args(["-Dfile.encoding=UTF-8"], java_major=8)
        self.assertEqual(result, ["-Dfile.encoding=UTF-8"])


class InstanceConfigTests(unittest.TestCase):
    def test_inherit_memory(self) -> None:
        settings = Settings()
        settings.memory_mode = "manual"
        settings.memory_mb = 3072
        instance = InstanceConfig(memory_mode="inherit")
        self.assertEqual(instance.memory_for(settings), 3072)

    def test_manual_memory(self) -> None:
        instance = InstanceConfig(memory_mode="manual", memory_mb=1024)
        self.assertEqual(instance.memory_for(Settings()), 1024)

    def test_auto_memory_uses_own_preset(self) -> None:
        instance = InstanceConfig(memory_mode="auto", jvm_preset="performance")
        self.assertEqual(instance.memory_for(Settings()), auto_memory_mb("performance"))

    def test_jvm_arguments_override(self) -> None:
        instance = InstanceConfig(memory_mode="manual", memory_mb=1536, jvm_preset="custom")
        args = instance.jvm_arguments(Settings(), java_major=17)
        self.assertIn("-Xmx1536M", args)

    def test_save_and_load(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "instances" / "demo" / "instance.json"
            config = InstanceConfig(name="demo", display_name="Демо")
            config.memory_mode = "manual"
            config.memory_mb = 2048
            config.save(path)
            loaded = InstanceConfig.load(path)
            self.assertEqual(loaded.display_name, "Демо")
            self.assertEqual(loaded.memory_mb, 2048)

    def test_load_missing_name_uses_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "instances" / "mycool" / "instance.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"display_name": "X"}), encoding="utf-8")
            loaded = InstanceConfig.load(path)
            self.assertEqual(loaded.name, "mycool")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
