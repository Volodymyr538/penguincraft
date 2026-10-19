"""Тесты клиента Modrinth (разбор ответов и подбор версий, без сети)."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from penguincraft.core.net import Http
from penguincraft.mods.modrinth import (
    ModVersion,
    Modrinth,
    SearchHit,
    VersionFile,
    format_downloads,
)


class FakeHttp:
    """Подменяет ``Http``: отдаёт заранее заданные ответы."""

    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get_json(self, url, headers=None):
        self.requested.append(url)
        for fragment, payload in self.responses.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected url {url}")


def version_file(name="mod.jar", sha1="a" * 40, primary=True):
    return {
        "url": f"https://cdn.example/{name}",
        "filename": name,
        "hashes": {"sha1": sha1, "sha512": ""},
        "size": 10,
        "primary": primary,
    }


class SearchHitTests(unittest.TestCase):
    def test_from_dict(self) -> None:
        hit = SearchHit.from_dict(
            {
                "project_id": "AANobbMI",
                "slug": "sodium",
                "title": "Sodium",
                "description": "быстро",
                "project_type": "mod",
                "downloads": 1000,
                "categories": ["fabric", "optimization"],
                "icon_url": "https://x/icon.webp",
                "license": {"id": "LGPL-3.0"},
            }
        )
        self.assertEqual(hit.project_id, "AANobbMI")
        self.assertEqual(hit.loaders, ["fabric"])
        self.assertEqual(hit.license, "LGPL-3.0")


class ModVersionTests(unittest.TestCase):
    def test_primary_file(self) -> None:
        version = ModVersion.from_dict(
            {
                "id": "v1",
                "project_id": "p1",
                "name": "1.0",
                "version_number": "1.0",
                "version_type": "release",
                "files": [version_file("a.jar", primary=False), version_file("b.jar")],
            }
        )
        self.assertEqual(version.primary_file.filename, "b.jar")

    def test_type_title(self) -> None:
        version = ModVersion.from_dict({"id": "x", "project_id": "y", "name": "", "version_number": "", "version_type": "beta"})
        self.assertEqual(version.type_title, "бета")

    def test_version_file_hash_selection(self) -> None:
        file = VersionFile.from_dict(version_file())
        self.assertEqual(file.sha1, "a" * 40)


class BestVersionTests(unittest.TestCase):
    def _modrinth(self, versions):
        http = FakeHttp({"/version": versions})
        return Modrinth(http)  # type: ignore[arg-type]

    @staticmethod
    def _raw(vid, number, *, version_type="release", loaders=None, game_versions=None):
        return {
            "id": vid,
            "project_id": "p",
            "name": "",
            "version_number": number,
            "version_type": version_type,
            "loaders": list(loaders or []),
            "game_versions": list(game_versions or []),
            "files": [version_file()],
        }

    def test_prefers_game_version_and_loader(self) -> None:
        client = self._modrinth(
            [
                self._raw("old", "0.1", loaders=["fabric"], game_versions=["1.19"]),
                self._raw("new", "0.2", loaders=["fabric"], game_versions=["1.20.1"]),
            ]
        )
        best = client.best_version("p", loader="fabric", game_version="1.20.1")
        self.assertEqual(best.id, "new")

    def test_prefers_release_over_beta(self) -> None:
        client = self._modrinth(
            [
                self._raw("beta", "2.0", version_type="beta"),
                self._raw("release", "1.0"),
            ]
        )
        best = client.best_version("p")
        self.assertEqual(best.id, "release")

    def test_no_versions(self) -> None:
        client = self._modrinth([])
        self.assertIsNone(client.best_version("p"))


class VersionByHashTests(unittest.TestCase):
    def test_short_hash_returns_none(self) -> None:
        client = Modrinth(FakeHttp({}))  # type: ignore[arg-type]
        self.assertIsNone(client.version_by_hash("abc"))

    def test_found(self) -> None:
        payload = {
            "id": "v1",
            "project_id": "p1",
            "name": "Sodium 1.0",
            "version_number": "1.0",
            "version_type": "release",
            "files": [version_file()],
        }
        client = Modrinth(FakeHttp({"/version_file/": payload}))  # type: ignore[arg-type]
        found = client.version_by_hash("a" * 40)
        self.assertEqual(found.project_id, "p1")

    def test_not_found_returns_none(self) -> None:
        client = Modrinth(FakeHttp({"/version_file/": ValueError("404")}))  # type: ignore[arg-type]
        self.assertIsNone(client.version_by_hash("b" * 40))


class DownloadFileTests(unittest.TestCase):
    def test_skips_when_hash_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            data = b"already-here"
            target = Path(tmp) / "mod.jar"
            target.write_bytes(data)
            file = VersionFile(
                url="https://cdn.example/mod.jar",
                filename="mod.jar",
                sha512=hashlib.sha512(data).hexdigest(),
                sha1=hashlib.sha1(data).hexdigest(),
                size=len(data),
            )
            client = Modrinth(FakeHttp({}))  # type: ignore[arg-type]
            self.assertEqual(client.download_file(file, tmp), target)

    def test_no_url_raises(self) -> None:
        from penguincraft.mods.modrinth import ModrinthError

        with TemporaryDirectory() as tmp:
            file = VersionFile(url="", filename="x.jar")
            client = Modrinth(FakeHttp({}))  # type: ignore[arg-type]
            with self.assertRaises(ModrinthError):
                client.download_file(file, tmp)


class FormatDownloadsTests(unittest.TestCase):
    def test_formatting(self) -> None:
        self.assertEqual(format_downloads(999), "999")
        self.assertEqual(format_downloads(1500), "2 тыс,")
        self.assertEqual(format_downloads(2_500_000), "2,5 млн")


class RealHttpConstructionTests(unittest.TestCase):
    def test_default_http_created(self) -> None:
        client = Modrinth()
        self.assertIsInstance(client.http, Http)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
