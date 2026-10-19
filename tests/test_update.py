"""Тесты проверки обновлений лаунчера."""

from __future__ import annotations

import unittest

from penguincraft.core.update import (
    parse_release,
    parse_version,
    version_is_newer,
)


class ParseVersionTests(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))

    def test_with_v_prefix(self) -> None:
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_suffix_ignored(self) -> None:
        self.assertEqual(parse_version("1.2.3-beta1"), (1, 2, 3))

    def test_empty(self) -> None:
        self.assertEqual(parse_version(""), (0,))

    def test_shorter_is_smaller(self) -> None:
        self.assertLess(parse_version("1.2"), parse_version("1.2.1"))


class VersionIsNewerTests(unittest.TestCase):
    def test_newer(self) -> None:
        self.assertTrue(version_is_newer("1.3.0", "1.2.0"))
        self.assertTrue(version_is_newer("2.0.0", "1.99.99"))

    def test_same(self) -> None:
        self.assertFalse(version_is_newer("1.2.0", "1.2.0"))

    def test_older(self) -> None:
        self.assertFalse(version_is_newer("1.1.0", "1.2.0"))


class ParseReleaseTests(unittest.TestCase):
    def test_parses_fields(self) -> None:
        release = parse_release(
            {
                "tag_name": "v1.3.0",
                "html_url": "https://github.com/x/y/releases/tag/v1.3.0",
                "body": "Заметки",
                "published_at": "2026-01-01T00:00:00Z",
                "assets": [{"name": "a.sh"}, {"name": "b.deb"}],
            }
        )
        self.assertEqual(release.version, "1.3.0")
        self.assertEqual(release.tag, "v1.3.0")
        self.assertEqual(release.assets, ["a.sh", "b.deb"])
        self.assertEqual(release.notes, "Заметки")

    def test_requires_tag(self) -> None:
        self.assertIsNone(parse_release({}))
        self.assertIsNone(parse_release("nope"))  # type: ignore[arg-type]

    def test_is_newer(self) -> None:
        release = parse_release({"tag_name": "v99.0.0"})
        self.assertTrue(release.is_newer)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
