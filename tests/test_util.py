"""Тесты мелких утилит: имена, размеры, maven, архивы."""

from __future__ import annotations

import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from penguincraft.core import util as util_module
from penguincraft.core.util import (
    MavenCoordinate,
    extract_zip,
    hash_file,
    human_size,
    platform_arch,
    sanitize_name,
    split_args,
    total_memory_mb,
)


class SanitizeNameTests(unittest.TestCase):
    def test_removes_illegal_characters(self) -> None:
        self.assertEqual(sanitize_name('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_strips_dots_and_spaces(self) -> None:
        self.assertEqual(sanitize_name("  ...Сборка...  "), "Сборка")

    def test_fallback_for_empty(self) -> None:
        self.assertEqual(sanitize_name("   ", fallback="instance"), "instance")

    def test_truncates_to_120(self) -> None:
        self.assertEqual(len(sanitize_name("x" * 500)), 120)

    def test_reserved_windows_names(self) -> None:
        # Windows не даст создать каталог с таким именем.
        self.assertEqual(sanitize_name("CON"), "_CON")
        self.assertEqual(sanitize_name("nul"), "_nul")
        self.assertEqual(sanitize_name("COM1"), "_COM1")
        self.assertEqual(sanitize_name("lpt9.txt"), "_lpt9.txt")
        # «CONSOLE» — не зарезервировано.
        self.assertEqual(sanitize_name("CONSOLE"), "CONSOLE")

    def test_no_trailing_space(self) -> None:
        self.assertEqual(sanitize_name("Сборка   "), "Сборка")


class SplitArgsTests(unittest.TestCase):
    def test_posix_quotes(self) -> None:
        self.assertEqual(split_args('--demo -Xmx2G "мой мир"'), ["--demo", "-Xmx2G", "мой мир"])

    def test_empty(self) -> None:
        self.assertEqual(split_args("   "), [])

    def test_windows_backslashes(self) -> None:
        # Без posix=False обратные слэши «съедаются», и путь ломается.
        with mock.patch.object(util_module.os, "name", "nt"):
            self.assertEqual(
                split_args(r"-Djava.home=C:\Java\jdk-21"),
                [r"-Djava.home=C:\Java\jdk-21"],
            )

    def test_broken_quotes_do_not_crash(self) -> None:
        self.assertEqual(split_args('"не закрытая'), ['"не', "закрытая"])


class TotalMemoryTests(unittest.TestCase):
    def test_positive(self) -> None:
        self.assertGreater(total_memory_mb(), 0)

    def test_windows_helper_never_raises(self) -> None:
        # На Linux ядро Windows недоступно — просто возвращаем 0.
        self.assertIsInstance(util_module._windows_total_memory_mb(), int)


class HumanSizeTests(unittest.TestCase):
    def test_bytes(self) -> None:
        self.assertEqual(human_size(0), "0 Б")
        self.assertEqual(human_size(512), "512 Б")

    def test_kilobytes(self) -> None:
        self.assertEqual(human_size(1536), "1.5 КБ")

    def test_megabytes(self) -> None:
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 МБ")

    def test_gigabytes(self) -> None:
        self.assertEqual(human_size(3 * 1024 ** 3), "3.0 ГБ")


class MavenCoordinateTests(unittest.TestCase):
    def test_parse_simple(self) -> None:
        coordinate = MavenCoordinate.parse("org.lwjgl:lwjgl:3.3.1")
        self.assertEqual(coordinate.group, "org.lwjgl")
        self.assertEqual(coordinate.artifact, "lwjgl")
        self.assertEqual(coordinate.version, "3.3.1")
        self.assertEqual(coordinate.file_name, "lwjgl-3.3.1.jar")
        self.assertEqual(
            coordinate.path, "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar"
        )

    def test_parse_classifier_and_extension(self) -> None:
        coordinate = MavenCoordinate.parse("org.lwjgl:lwjgl:3.3.1:natives-linux@jar")
        self.assertEqual(coordinate.classifier, "natives-linux")
        self.assertEqual(coordinate.file_name, "lwjgl-3.3.1-natives-linux.jar")

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            MavenCoordinate.parse("not-a-coordinate")

    def test_roundtrip(self) -> None:
        text = "net.fabricmc:fabric-loader:0.15.0"
        self.assertEqual(MavenCoordinate.parse(text).to_string(), text)


class HashTests(unittest.TestCase):
    def test_hash_file(self) -> None:
        import hashlib

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            path.write_bytes(b"penguin")
            self.assertEqual(hash_file(path, "sha1"), hashlib.sha1(b"penguin").hexdigest())
            self.assertEqual(
                hash_file(path, "sha512"), hashlib.sha512(b"penguin").hexdigest()
            )


class ExtractZipTests(unittest.TestCase):
    def _make_zip(self, path: Path, entries) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries:
                archive.writestr(name, data)

    def test_extracts_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "a.zip"
            self._make_zip(archive, [("dir/file.txt", "hello"), ("root.txt", "world")])
            target = root / "out"
            extract_zip(archive, target)
            self.assertEqual((target / "dir" / "file.txt").read_text(), "hello")
            self.assertEqual((target / "root.txt").read_text(), "world")

    def test_blocks_path_traversal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "evil.zip"
            self._make_zip(archive, [("../outside.txt", "nope")])
            target = root / "out"
            extract_zip(archive, target)
            self.assertFalse((root / "outside.txt").exists())
            self.assertFalse((target.parent / "outside.txt").exists())

    def test_exclude_and_prefix_strip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "a.zip"
            self._make_zip(
                archive,
                [
                    ("META-INF/MANIFEST.MF", "meta"),
                    ("mod/thing.jar", "jar"),
                    ("mod/readme.txt", "text"),
                ],
            )
            target = root / "out"
            extract_zip(archive, target, exclude=("META-INF/",), prefix_strip="mod/")
            self.assertTrue((target / "thing.jar").exists())
            self.assertTrue((target / "readme.txt").exists())
            self.assertFalse((target / "META-INF").exists())


class PlatformArchTests(unittest.TestCase):
    def test_returns_non_empty_string(self) -> None:
        self.assertTrue(platform_arch())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
