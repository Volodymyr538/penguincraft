"""Вычисление правил (``rules``) из манифеста Minecraft.

Правила управляют тем, какие библиотеки и аргументы применяются на
текущей платформе.  Логика повторяет поведение официального лаунчера:
если правила есть, по умолчанию элемент *запрещён*, и применяется
последнее подошедшее правило.
"""

from __future__ import annotations

import platform
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .paths import natives_for_os

# ---------------------------------------------------------------------------
# Контекст платформы
# ---------------------------------------------------------------------------


def os_name() -> str:
    """``name`` текущей ОС в терминах манифеста."""
    return natives_for_os()


def os_version() -> str:
    return platform.release() or ""


def os_arch() -> str:
    """``arch`` в терминах Mojang: ``x86``, ``x64``, ``arm64``."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    return machine


def default_platform(features: Optional[Mapping[str, bool]] = None) -> Dict[str, Any]:
    return {
        "name": os_name(),
        "version": os_version(),
        "arch": os_arch(),
        "features": dict(features or {}),
    }


# ---------------------------------------------------------------------------
# Правила
# ---------------------------------------------------------------------------


def _os_matches(rule_os: Mapping[str, Any], plat: Mapping[str, Any]) -> bool:
    name = rule_os.get("name")
    if name and name != plat.get("name"):
        return False

    arch = rule_os.get("arch")
    if arch and arch != plat.get("arch"):
        return False

    version_regex = rule_os.get("version")
    if version_regex:
        try:
            if not re.match(version_regex, str(plat.get("version", ""))):
                return False
        except re.error:
            return False
    return True


def _rule_matches(rule: Mapping[str, Any], plat: Mapping[str, Any]) -> bool:
    rule_os = rule.get("os")
    if rule_os is not None and not _os_matches(rule_os, plat):
        return False
    features = rule.get("features")
    if features:
        actual = plat.get("features", {})
        for key, expected in features.items():
            if bool(actual.get(key, False)) != bool(expected):
                return False
    return True


def rules_allow(
    rules: Optional[Sequence[Mapping[str, Any]]],
    platform: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Разрешают ли правила элемент на текущей платформе."""
    if not rules:
        return True
    plat = platform if platform is not None else default_platform()
    allowed = False
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        if _rule_matches(rule, plat):
            allowed = rule.get("action") == "allow"
    return allowed


# ---------------------------------------------------------------------------
# Аргументы
# ---------------------------------------------------------------------------


def resolve_arguments(
    arguments: Any,
    platform: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Превращает блок ``arguments`` в плоский список строк.

    Поддерживает и современный формат (``arguments.game`` / ``arguments.jvm``
    со словарями и правилами), и устаревший ``minecraftArguments``.
    """
    plat = platform if platform is not None else default_platform()
    if arguments is None:
        return []
    if isinstance(arguments, str):
        return arguments.split()
    if isinstance(arguments, Mapping):
        result: List[str] = []
        # порядок: сначала jvm, затем game — лаунчер вызывает это отдельно,
        # но если передали словарь целиком, сохраняем оба блока.
        for key in ("game", "jvm"):
            if key in arguments:
                result.extend(resolve_arguments(arguments[key], plat))
        return result

    result = []
    for entry in arguments:
        if isinstance(entry, str):
            result.append(entry)
            continue
        if not isinstance(entry, Mapping):
            continue
        if not rules_allow(entry.get("rules"), plat):
            continue
        value = entry.get("value")
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, Iterable):
            result.extend(str(item) for item in value)
    return result


# ---------------------------------------------------------------------------
# Подстановка ${...}
# ---------------------------------------------------------------------------


_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def substitute(value: str, variables: Mapping[str, Any]) -> str:
    """Заменяет ``${ключ}`` на значения из *variables*."""

    def replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return ""

    return _PLACEHOLDER_RE.sub(replace, value)


def substitute_all(values: Iterable[str], variables: Mapping[str, Any]) -> List[str]:
    return [substitute(item, variables) for item in values]


def collect_placeholders(value: str) -> List[str]:
    return _PLACEHOLDER_RE.findall(value)
