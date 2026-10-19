"""Тесты поддержки Windows: пути, Java, иконка `.exe` и версия сборки.

Часть проверок невозможно выполнить «по-настоящему» на Linux (Windows-ветки
и сборка PyInstaller), поэтому мы либо подставляем признак ОС, либо
проверяем структуру файлов, которые создаёт упаковка.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from penguincraft import APP_NAME, APP_VERSION
from penguincraft.core import java, paths
from penguincraft.core.util import ensure_utf8_console, is_windows

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PACKAGING = ROOT / "packaging" / "windows"

if str(WINDOWS_PACKAGING) not in sys.path:
    sys.path.insert(0, str(WINDOWS_PACKAGING))


class DefaultRootTests(unittest.TestCase):
    def test_env_override_wins(self) -> None:
        with mock.patch.dict(os.environ, {"PENGUINCRAFT_HOME": "/tmp/опыт"}, clear=False):
            self.assertEqual(paths.default_root(), Path("/tmp/опыт"))

    def test_windows_uses_localappdata(self) -> None:
        env = {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop(paths.ENV_ROOT, None)
            with mock.patch.object(paths, "is_windows", lambda: True):
                root = paths.default_root()
        text = str(root).replace("\\", "/")
        self.assertIn("AppData/Local", text)
        self.assertTrue(text.endswith(APP_NAME))
        # Роуминг-профиль использовать нельзя: данные весят гигабайты.
        self.assertNotIn("Roaming", text)

    def test_windows_falls_back_to_appdata(self) -> None:
        env = {"APPDATA": r"C:\Users\tester\AppData\Roaming"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop(paths.ENV_ROOT, None)
            os.environ.pop("LOCALAPPDATA", None)
            with mock.patch.object(paths, "is_windows", lambda: True):
                root = paths.default_root()
        self.assertTrue(str(root).endswith(APP_NAME))

    def test_linux_uses_xdg_or_home(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paths.ENV_ROOT, None)
            os.environ.pop("XDG_DATA_HOME", None)
            with mock.patch.object(paths, "is_windows", lambda: False):
                root = paths.default_root()
        self.assertEqual(root, Path.home() / ".local" / "share" / "penguincraft")


class NativesTests(unittest.TestCase):
    def test_matches_current_platform(self) -> None:
        value = paths.natives_for_os()
        self.assertIn(value, ("linux", "windows", "osx"))
        if is_windows():
            self.assertEqual(value, "windows")
        elif sys.platform == "darwin":
            self.assertEqual(value, "osx")
        else:
            self.assertEqual(value, "linux")


class JavaPlatformTests(unittest.TestCase):
    def test_executable_name_matches_platform(self) -> None:
        expected = "java.exe" if is_windows() else "java"
        self.assertEqual(java.JAVA_EXE, expected)

    def test_relative_paths_use_executable_name(self) -> None:
        for relative in java._JAVA_RELATIVE:
            self.assertTrue(relative.endswith(java.JAVA_EXE), relative)

    def test_windows_roots_cover_vendors(self) -> None:
        env = {
            "ProgramFiles": r"C:\Program Files",
            "ProgramFiles(x86)": r"C:\Program Files (x86)",
            "LOCALAPPDATA": r"C:\Users\tester\AppData\Local",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            roots = java._java_roots(windows=True)
        text = [str(item).replace("\\", "/") for item in roots]
        self.assertTrue(any("Eclipse Adoptium" in item for item in text))
        self.assertTrue(any("Java" in item for item in text))
        self.assertTrue(any("Programs" in item and "Adoptium" in item for item in text))
        self.assertFalse(any(item.startswith("/usr") for item in text))

    def test_posix_roots_unchanged(self) -> None:
        roots = java._java_roots(windows=False)
        self.assertIn(Path("/usr/lib/jvm"), roots)
        self.assertIn(Path("/opt"), roots)
        self.assertFalse(any("ProgramFiles" in str(item) for item in roots))

    def test_candidates_include_java_home(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "jdk-21"
            (home / "bin").mkdir(parents=True)
            exe = home / "bin" / java.JAVA_EXE
            exe.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"JAVA_HOME": str(home)}, clear=False):
                candidates = java._candidate_paths()
        self.assertIn((home / "bin" / java.JAVA_EXE, "java_home"), candidates)

    def test_find_java_in_nested_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "jdk-21.0.1+12" / "bin"
            nested.mkdir(parents=True)
            (nested / java.JAVA_EXE).write_text("", encoding="utf-8")
            self.assertEqual(java._find_java_in(root), nested / java.JAVA_EXE)

    def test_adoptium_url_targets_windows(self) -> None:
        with mock.patch.object(java, "is_windows", lambda: True):
            url = java._adoptium_url(21, "x64")
        self.assertIn("/windows/", url)
        self.assertIn("/21/", url)

    def test_adoptium_url_targets_linux(self) -> None:
        with mock.patch.object(java, "is_windows", lambda: False), \
                mock.patch.object(java, "is_macos", lambda: False):
            url = java._adoptium_url(17, "x64")
        self.assertIn("/linux/", url)
        self.assertIn("/17/", url)

    def test_windows_download_uses_zip(self) -> None:
        """Adoptium под Windows отдаёт zip — проверяем, что имя файла и
        распаковка соответствуют платформе."""
        captured: dict[str, object] = {}

        class FakeHttp:
            def download(self, url, destination, **kwargs):  # noqa: D401, ANN001
                captured["url"] = url
                captured["path"] = destination
                import zipfile

                # Пустой, но настоящий zip: распаковка пройдёт, а Java
                # внутри не окажется — этого достаточно для проверки.
                with zipfile.ZipFile(destination, "w"):
                    pass

        with TemporaryDirectory() as tmp:
            with mock.patch.object(java, "is_windows", lambda: True), \
                    mock.patch.object(java, "platform_arch", lambda: "x64"):
                from penguincraft.core.paths import Paths

                with self.assertRaises(RuntimeError):
                    # Архив пустой, поэтому распаковка не найдёт java —
                    # нам важен только путь, по которому качали.
                    java.download_java(Paths(Path(tmp)), 21, FakeHttp())
        self.assertTrue(str(captured["path"]).endswith("jre-21-x64.zip"))
        self.assertIn("/windows/", str(captured["url"]))


class Utf8ConsoleTests(unittest.TestCase):
    def test_does_not_raise_and_is_idempotent(self) -> None:
        ensure_utf8_console()
        ensure_utf8_console()

    def test_survives_none_streams(self) -> None:
        # В собранном --windowed exe потоки равны None.
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            ensure_utf8_console()


class PackagingTests(unittest.TestCase):
    """Проверяет то, что иначе проверялось бы только на Windows-раннере."""

    def test_icon_is_valid_ico_with_png_entry(self) -> None:
        import make_icon

        icon = make_icon.build_ico((ROOT / "penguincraft" / "ui" / "assets" / "penguincraft.png").read_bytes())
        reserved, kind, count = struct.unpack("<HHH", icon[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 1))
        entry_size = struct.calcsize("<BBBBHHII")
        width, height, colors, _res, planes, bits, size, offset = struct.unpack(
            "<BBBBHHII", icon[6 : 6 + entry_size]
        )
        self.assertEqual((width, height, colors, planes, bits), (0, 0, 0, 1, 32))
        self.assertEqual(size, len(icon) - offset)
        self.assertEqual(icon[offset : offset + 8], b"\x89PNG\r\n\x1a\n")

    def test_make_icon_writes_file(self) -> None:
        import make_icon

        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "app.ico"
            make_icon.make_icon(ROOT / "penguincraft" / "ui" / "assets" / "penguincraft.png", target)
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes()[:4], b"\x00\x00\x01\x00")

    def test_make_icon_rejects_non_png(self) -> None:
        import make_icon

        with self.assertRaises(ValueError):
            make_icon.build_ico(b"not a png at all")

    def test_build_script_reads_real_version(self) -> None:
        import build

        self.assertEqual(build.read_version(), APP_VERSION)

    def test_iss_declares_setup_and_guids(self) -> None:
        text = (WINDOWS_PACKAGING / "penguincraft.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{", text)
        self.assertIn("PrivilegesRequired=lowest", text)
        self.assertIn("DefaultDirName={localappdata}", text)
        self.assertIn("PenguinCraft.exe", text)

    def test_workflow_builds_windows_exe(self) -> None:
        text = (ROOT / ".github" / "workflows" / "windows.yml").read_text(encoding="utf-8")
        self.assertIn("windows-latest", text)
        self.assertIn("packaging/windows/build.py", text)
        self.assertIn("gh release", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
