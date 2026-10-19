"""Тесты правил манифеста и подстановки переменных."""

from __future__ import annotations

import unittest

from penguincraft.core.rules import resolve_arguments, rules_allow, substitute, substitute_all

LINUX = {"name": "linux", "version": "6.1", "arch": "x64", "features": {}}
WINDOWS = {"name": "windows", "version": "10.0", "arch": "x64", "features": {}}


class RulesAllowTests(unittest.TestCase):
    def test_without_rules_allowed(self) -> None:
        self.assertTrue(rules_allow(None, LINUX))
        self.assertTrue(rules_allow([], LINUX))

    def test_allow_rule_matching(self) -> None:
        rules = [{"action": "allow", "os": {"name": "linux"}}]
        self.assertTrue(rules_allow(rules, LINUX))
        self.assertFalse(rules_allow(rules, WINDOWS))

    def test_disallow_rule(self) -> None:
        rules = [
            {"action": "allow"},
            {"action": "disallow", "os": {"name": "linux"}},
        ]
        self.assertFalse(rules_allow(rules, LINUX))
        self.assertTrue(rules_allow(rules, WINDOWS))

    def test_arch_and_version(self) -> None:
        rules = [{"action": "allow", "os": {"arch": "x86"}}]
        self.assertFalse(rules_allow(rules, LINUX))
        rules = [{"action": "allow", "os": {"version": "^6\\."}}]
        self.assertTrue(rules_allow(rules, LINUX))

    def test_features(self) -> None:
        rules = [{"action": "allow", "features": {"is_demo_user": True}}]
        self.assertFalse(rules_allow(rules, LINUX))
        plat = dict(LINUX, features={"is_demo_user": True})
        self.assertTrue(rules_allow(rules, plat))


class ResolveArgumentsTests(unittest.TestCase):
    def test_string_form(self) -> None:
        self.assertEqual(resolve_arguments("--a b --c", LINUX), ["--a", "b", "--c"])

    def test_dict_form_order(self) -> None:
        arguments = {
            "game": ["--game"],
            "jvm": ["--jvm"],
        }
        self.assertEqual(resolve_arguments(arguments, LINUX), ["--game", "--jvm"])

    def test_rules_filtered(self) -> None:
        arguments = [
            "--always",
            {"rules": [{"action": "allow", "os": {"name": "windows"}}], "value": "--win"},
            {"rules": [{"action": "allow", "os": {"name": "linux"}}], "value": ["--lin"]},
        ]
        self.assertEqual(resolve_arguments(arguments, LINUX), ["--always", "--lin"])

    def test_none_returns_empty(self) -> None:
        self.assertEqual(resolve_arguments(None, LINUX), [])


class SubstituteTests(unittest.TestCase):
    def test_simple(self) -> None:
        self.assertEqual(
            substitute("${auth_player_name}", {"auth_player_name": "Стив"}), "Стив"
        )

    def test_missing_becomes_empty(self) -> None:
        self.assertEqual(substitute("a${nope}b", {}), "ab")

    def test_all(self) -> None:
        result = substitute_all(["--x", "${v}"], {"v": "1"})
        self.assertEqual(result, ["--x", "1"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
