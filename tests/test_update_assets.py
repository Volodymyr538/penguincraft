"""Тесты выбора файла обновления из релиза GitHub."""

from __future__ import annotations

import unittest

from penguincraft.core.update import (
    asset_kind_title,
    classify_asset,
    parse_release,
    pick_asset,
)


def release(*names: str):
    assets = []
    urls = {}
    sizes = {}
    for name in names:
        url = f"https://example.invalid/{name}"
        assets.append({"name": name, "browser_download_url": url, "size": 10})
        urls[name] = url
        sizes[name] = 10
    return parse_release(
        {
            "tag_name": "v1.5.0",
            "assets": assets,
        }
    )


class ClassifyTests(unittest.TestCase):
    def test_kinds(self) -> None:
        self.assertEqual(classify_asset("PenguinCraft-1.5.0-setup.exe"), "windows-setup")
        self.assertEqual(
            classify_asset("PenguinCraft-1.5.0-portable.exe"), "windows-portable"
        )
        self.assertEqual(
            classify_asset("PenguinCraft-1.5.0-installer.sh"), "linux-installer"
        )
        self.assertEqual(classify_asset("penguincraft_1.5.0_all.deb"), "linux-deb")
        self.assertEqual(classify_asset("notes.txt"), "other")

    def test_titles(self) -> None:
        self.assertIn("Windows", asset_kind_title("windows-setup"))
        self.assertIn("Linux", asset_kind_title("linux-installer"))


class PickAssetTests(unittest.TestCase):
    def test_windows_prefers_setup(self) -> None:
        info = release(
            "PenguinCraft-1.5.0-portable.exe",
            "PenguinCraft-1.5.0-setup.exe",
        )
        asset = pick_asset(info, platform="win32")
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "windows-setup")
        self.assertTrue(asset.url.endswith("-setup.exe"))

    def test_windows_portable_preference(self) -> None:
        info = release(
            "PenguinCraft-1.5.0-setup.exe",
            "PenguinCraft-1.5.0-portable.exe",
        )
        asset = pick_asset(info, platform="win32", portable=True)
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "windows-portable")

    def test_linux_prefers_installer_over_deb(self) -> None:
        info = release(
            "penguincraft_1.5.0_all.deb",
            "PenguinCraft-1.5.0-installer.sh",
        )
        asset = pick_asset(info, platform="linux")
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "linux-installer")

    def test_no_url_is_skipped(self) -> None:
        info = parse_release(
            {
                "tag_name": "v1.5.0",
                "assets": [{"name": "PenguinCraft-1.5.0-installer.sh"}],
            }
        )
        self.assertIsNone(pick_asset(info, platform="linux"))

    def test_unknown_platform(self) -> None:
        info = release("PenguinCraft-1.5.0-installer.sh")
        self.assertIsNone(pick_asset(info, platform="darwin"))

    def test_asset_size_kept(self) -> None:
        info = release("PenguinCraft-1.5.0-installer.sh")
        asset = pick_asset(info, platform="linux")
        self.assertEqual(asset.size, 10)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
