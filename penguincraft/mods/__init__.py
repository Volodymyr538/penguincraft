"""Работа с модами: каталог Modrinth и локальные файлы."""

from .manager import (
    LocalMod,
    delete_mod,
    find_duplicates,
    import_mods,
    list_mods,
    parse_mod_metadata,
    set_enabled,
)
from .modrinth import Modrinth, ModVersion, ModrinthError, SearchHit, SearchResult, VersionFile
from .mrpack import (
    ModpackError,
    ModpackIndex,
    detect_modpack_kind,
    extract_mrpack_meta,
    install_modpack,
    read_index,
)

__all__ = [
    "LocalMod",
    "list_mods",
    "set_enabled",
    "delete_mod",
    "import_mods",
    "find_duplicates",
    "parse_mod_metadata",
    "Modrinth",
    "ModrinthError",
    "SearchHit",
    "SearchResult",
    "ModVersion",
    "VersionFile",
    "ModpackError",
    "ModpackIndex",
    "read_index",
    "install_modpack",
    "detect_modpack_kind",
    "extract_mrpack_meta",
]
