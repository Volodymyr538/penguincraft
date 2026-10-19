"""Тесты проверки зависимостей модов."""

from __future__ import annotations

import unittest

from penguincraft.mods.dependencies import (
    DependencyPlan,
    ResolvedDependency,
    disabled_requirements,
    missing_local,
    resolve_for_version,
)
from penguincraft.mods.manager import LocalMod
from penguincraft.mods.modrinth import ModVersion


def version(pid: str, vid: str, deps=None, name: str = "") -> ModVersion:
    return ModVersion(
        id=vid,
        project_id=pid,
        name=name or pid,
        version_number="1.0",
        version_type="release",
        dependencies=list(deps or []),
    )


class FakeModrinth:
    """Минимальная заглушка вместо сети."""

    def __init__(self, versions):
        self._versions = versions
        self.calls = []

    def version(self, version_id: str):
        self.calls.append(("version", version_id))
        return self._versions.get(version_id)

    def best_version(self, project_id: str, *, loader: str = "", game_version: str = ""):
        self.calls.append(("best", project_id))
        return self._versions.get(project_id)


class MissingLocalTests(unittest.TestCase):
    def test_reports_missing_and_ignores_builtin(self) -> None:
        mods = [
            LocalMod(path=None, mod_id="sodium", depends={"fabric-api": "*", "minecraft": ">=1"}, name="Sodium"),
            LocalMod(path=None, mod_id="fabric-api", depends={}, name="Fabric API"),
        ]
        result = missing_local(mods)
        # fabric-api установлен, minecraft — встроенный
        self.assertEqual(result, {})

    def test_reports_real_missing(self) -> None:
        mods = [
            LocalMod(path=None, mod_id="sodium", depends={"fabric-api": "*"}, name="Sodium"),
        ]
        self.assertEqual(missing_local(mods), {"Sodium": ["fabric-api"]})

    def test_ignores_disabled_mods(self) -> None:
        mods = [
            LocalMod(path=None, mod_id="a", depends={"b": "*"}, name="A", enabled=False),
            LocalMod(path=None, mod_id="b", depends={}, name="B"),
        ]
        self.assertEqual(missing_local(mods), {})

    def test_ignores_disabled_dependency_as_satisfied(self) -> None:
        # Выключенный мод игрой не загружается, поэтому для missing_local
        # зависимость считается отсутствующей (причину уточняет
        # disabled_requirements).
        mods = [
            LocalMod(path=None, mod_id="a", depends={"b": "*"}, name="A"),
            LocalMod(path=None, mod_id="b", depends={}, name="B", enabled=False),
        ]
        self.assertEqual(missing_local(mods), {"A": ["b"]})


class DisabledRequirementsTests(unittest.TestCase):
    def test_finds_disabled(self) -> None:
        mods = [
            LocalMod(path=None, mod_id="a", depends={"b": "*"}, name="A"),
            LocalMod(path=None, mod_id="b", depends={}, name="B", enabled=False),
        ]
        self.assertEqual(disabled_requirements(mods), {"A": ["b"]})


class ResolveForVersionTests(unittest.TestCase):
    def test_collects_required_recursively(self) -> None:
        fake = FakeModrinth(
            {
                "lib": version("lib", "lib-v1", deps=[{"project_id": "core", "dependency_type": "required"}]),
                "core": version("core", "core-v1"),
            }
        )
        root = version("root", "root-v1", deps=[
            {"project_id": "lib", "dependency_type": "required"},
            {"project_id": "opt", "dependency_type": "optional"},
            {"project_id": "bad", "dependency_type": "incompatible"},
        ])
        plan = resolve_for_version(fake, root, installed=["bad"])
        self.assertEqual([item.project_id for item in plan.required], ["lib", "core"])
        self.assertEqual(plan.optional, [])
        self.assertEqual(plan.conflicts, ["bad"])

    def test_includes_optional_when_asked(self) -> None:
        fake = FakeModrinth({"opt": version("opt", "opt-v1")})
        root = version("root", "root-v1", deps=[{"project_id": "opt", "dependency_type": "optional"}])
        plan = resolve_for_version(fake, root, include_optional=True)
        self.assertEqual([item.project_id for item in plan.optional], ["opt"])

    def test_skips_already_installed(self) -> None:
        fake = FakeModrinth({"lib": version("lib", "lib-v1")})
        root = version("root", "root-v1", deps=[{"project_id": "lib", "dependency_type": "required"}])
        plan = resolve_for_version(fake, root, installed=["lib"])
        self.assertEqual(plan.required, [])

    def test_unresolved_dependency(self) -> None:
        fake = FakeModrinth({})
        root = version("root", "root-v1", deps=[{"project_id": "ghost", "dependency_type": "required"}])
        plan = resolve_for_version(fake, root)
        self.assertEqual(plan.required, [])
        self.assertEqual(plan.unresolved, ["ghost"])

    def test_cycle_is_safe(self) -> None:
        fake = FakeModrinth(
            {
                "a": version("a", "a-v1", deps=[{"project_id": "b", "dependency_type": "required"}]),
                "b": version("b", "b-v1", deps=[{"project_id": "a", "dependency_type": "required"}]),
            }
        )
        root = version("root", "root-v1", deps=[{"project_id": "a", "dependency_type": "required"}])
        plan = resolve_for_version(fake, root)
        self.assertEqual(sorted(item.project_id for item in plan.required), ["a", "b"])

    def test_explicit_version_used(self) -> None:
        fake = FakeModrinth({"lib": version("lib", "lib-v2")})
        root = version("root", "root-v1", deps=[
            {"project_id": "lib", "version_id": "lib-v2", "dependency_type": "required"}
        ])
        plan = resolve_for_version(fake, root)
        self.assertEqual([item.version.id for item in plan.required], ["lib-v2"])

    def test_plan_bool(self) -> None:
        self.assertFalse(DependencyPlan())
        self.assertTrue(DependencyPlan(required=[ResolvedDependency("a", version("a", "1"))]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
