"""Оформление интерфейса: палитры, шрифты и стили ttk.

Тема применяется целиком к корневому окну: после смены темы интерфейс
пересобирается заново (см. :meth:`penguincraft.ui.app.LauncherApp.set_theme`).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict

# ---------------------------------------------------------------------------
# Палитры
# ---------------------------------------------------------------------------

DARK: Dict[str, str] = {
    "bg": "#1b1e24",
    "panel": "#23272f",
    "panel_alt": "#2b3039",
    "sidebar": "#15171c",
    "hover": "#333945",
    "active": "#3c4351",
    "text": "#e7eaf0",
    "muted": "#98a2b3",
    "border": "#343a45",
    "accent": "#57b6ff",
    "accent_dark": "#3d92d6",
    "accent_text": "#0f1620",
    "input": "#1f232a",
    "success": "#4ecb71",
    "danger": "#ff6b6b",
    "warning": "#f2b34b",
    "tree_alt": "#262b33",
}

LIGHT: Dict[str, str] = {
    "bg": "#f3f5f9",
    "panel": "#ffffff",
    "panel_alt": "#eceff5",
    "sidebar": "#e6eaf2",
    "hover": "#dde3ee",
    "active": "#cfd8e8",
    "text": "#1b2431",
    "muted": "#5c6675",
    "border": "#d3dae5",
    "accent": "#2f7fd1",
    "accent_dark": "#2567ab",
    "accent_text": "#ffffff",
    "input": "#ffffff",
    "success": "#2f9e54",
    "danger": "#d64545",
    "warning": "#c98a1e",
    "tree_alt": "#f7f9fc",
}

#: Кандидаты шрифтов в порядке предпочтения (Crostini → Linux → прочее).
FONT_CANDIDATES = (
    "Noto Sans",
    "DejaVu Sans",
    "Ubuntu",
    "Cantarell",
    "Segoe UI",
    "Liberation Sans",
    "Arial",
    "Helvetica",
)

MONO_CANDIDATES = (
    "JetBrains Mono",
    "Fira Code",
    "Noto Sans Mono",
    "DejaVu Sans Mono",
    "Ubuntu Mono",
    "Liberation Mono",
    "Courier New",
    "Courier",
)


def palette(name: str) -> Dict[str, str]:
    """Возвращает словарь цветов для темы ``dark``/``light``."""
    return LIGHT if str(name).lower() == "light" else DARK


def _families(root: tk.Misc):
    try:
        return set(tkfont.families(root))
    except tk.TclError:  # pragma: no cover - сломанный Tk
        return set()


def _pick(root: tk.Misc, candidates, fallback: str) -> str:
    available = _families(root)
    for name in candidates:
        if name in available:
            return name
    return fallback


class Theme:
    """Набор цветов, шрифтов и настроенных стилей ttk."""

    def __init__(self, root: tk.Misc, name: str = "dark") -> None:
        self.root = root
        self.name = "light" if str(name).lower() == "light" else "dark"
        self.colors = palette(self.name)
        self.family = _pick(root, FONT_CANDIDATES, "TkDefaultFont")
        self.mono = _pick(root, MONO_CANDIDATES, "TkFixedFont")
        self.style = ttk.Style(root)
        self._configure_fonts()
        self._configure_ttk()

    # ------------------------------------------------------------------
    def _configure_fonts(self) -> None:
        for named, size, weight in (
            ("TkDefaultFont", 10, "normal"),
            ("TkTextFont", 10, "normal"),
            ("TkMenuFont", 10, "normal"),
            ("TkTooltipFont", 9, "normal"),
            ("TkHeadingFont", 10, "bold"),
        ):
            try:
                tkfont.nametofont(named).configure(family=self.family, size=size, weight=weight)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(family=self.mono, size=10)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    def _configure_ttk(self) -> None:
        c = self.colors
        style = self.style
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass

        try:
            self.root.configure(bg=c["bg"])
        except tk.TclError:  # pragma: no cover - root может не поддерживать
            pass

        style.configure(
            ".",
            background=c["bg"],
            foreground=c["text"],
            fieldbackground=c["input"],
            bordercolor=c["border"],
            focuscolor=c["accent"],
            font=(self.family, 10),
        )

        # -- контейнеры ---------------------------------------------------
        style.configure("TFrame", background=c["bg"])
        style.configure("Panel.TFrame", background=c["panel"])
        style.configure("Card.TFrame", background=c["panel"])
        style.configure("Alt.TFrame", background=c["panel_alt"])
        style.configure("Sidebar.TFrame", background=c["sidebar"])
        style.configure("Header.TFrame", background=c["panel"])
        style.configure("Footer.TFrame", background=c["panel_alt"])

        # -- подписи ------------------------------------------------------
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        style.configure("Card.TLabel", background=c["panel"], foreground=c["text"])
        style.configure("CardMuted.TLabel", background=c["panel"], foreground=c["muted"])
        style.configure(
            "CardTitle.TLabel",
            background=c["panel"],
            foreground=c["text"],
            font=(self.family, 12, "bold"),
        )
        style.configure(
            "PageTitle.TLabel",
            background=c["bg"],
            foreground=c["text"],
            font=(self.family, 19, "bold"),
        )
        style.configure(
            "PageSubtitle.TLabel",
            background=c["bg"],
            foreground=c["muted"],
            font=(self.family, 10),
        )
        style.configure(
            "SidebarTitle.TLabel",
            background=c["sidebar"],
            foreground=c["text"],
            font=(self.family, 14, "bold"),
        )
        style.configure("SidebarMuted.TLabel", background=c["sidebar"], foreground=c["muted"])
        style.configure("Footer.TLabel", background=c["panel_alt"], foreground=c["text"])
        style.configure("FooterMuted.TLabel", background=c["panel_alt"], foreground=c["muted"])
        style.configure(
            "Stat.TLabel",
            background=c["panel"],
            foreground=c["accent"],
            font=(self.family, 16, "bold"),
        )
        style.configure("Danger.TLabel", background=c["bg"], foreground=c["danger"])
        style.configure("Success.TLabel", background=c["bg"], foreground=c["success"])
        style.configure("CardSuccess.TLabel", background=c["panel"], foreground=c["success"])
        style.configure("CardDanger.TLabel", background=c["panel"], foreground=c["danger"])

        # -- группы -------------------------------------------------------
        style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"], relief="solid")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"])

        # -- кнопки -------------------------------------------------------
        style.configure(
            "TButton",
            background=c["panel_alt"],
            foreground=c["text"],
            bordercolor=c["border"],
            focuscolor=c["panel_alt"],
            borderwidth=1,
            relief="flat",
            padding=(12, 6),
        )
        style.map(
            "TButton",
            background=[("pressed", c["active"]), ("active", c["hover"]), ("disabled", c["panel_alt"])],
            foreground=[("disabled", c["muted"])],
            bordercolor=[("focus", c["accent"])],
        )
        style.configure(
            "Accent.TButton",
            background=c["accent"],
            foreground=c["accent_text"],
            bordercolor=c["accent"],
            focuscolor=c["accent"],
            padding=(16, 7),
        )
        style.map(
            "Accent.TButton",
            background=[("pressed", c["accent_dark"]), ("active", c["accent_dark"]), ("disabled", c["border"])],
            foreground=[("disabled", c["muted"])],
        )
        style.configure(
            "Danger.TButton",
            background=c["panel_alt"],
            foreground=c["danger"],
            bordercolor=c["border"],
            padding=(12, 6),
        )
        style.map(
            "Danger.TButton",
            background=[("pressed", c["active"]), ("active", c["hover"])],
        )
        style.configure(
            "Ghost.TButton",
            background=c["panel"],
            foreground=c["muted"],
            borderwidth=0,
            padding=(8, 4),
        )
        style.map(
            "Ghost.TButton",
            background=[("active", c["hover"]), ("pressed", c["active"])],
            foreground=[("active", c["text"])],
        )
        style.configure(
            "Nav.TButton",
            background=c["sidebar"],
            foreground=c["muted"],
            borderwidth=0,
            anchor="w",
            padding=(16, 9),
            font=(self.family, 10),
        )
        style.map(
            "Nav.TButton",
            background=[("active", c["hover"])],
            foreground=[("active", c["text"])],
        )
        style.configure(
            "NavActive.TButton",
            background=c["panel_alt"],
            foreground=c["accent"],
            borderwidth=0,
            anchor="w",
            padding=(16, 9),
            font=(self.family, 10, "bold"),
        )
        style.map(
            "NavActive.TButton",
            background=[("active", c["panel_alt"])],
            foreground=[("active", c["accent"])],
        )

        # -- поля ввода ---------------------------------------------------
        style.configure(
            "TEntry",
            fieldbackground=c["input"],
            foreground=c["text"],
            bordercolor=c["border"],
            insertcolor=c["text"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            padding=(6, 5),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", c["accent"])],
            fieldbackground=[("disabled", c["panel_alt"])],
            foreground=[("disabled", c["muted"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=c["input"],
            background=c["panel_alt"],
            foreground=c["text"],
            bordercolor=c["border"],
            arrowcolor=c["muted"],
            padding=(6, 4),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["input"]), ("disabled", c["panel_alt"])],
            bordercolor=[("focus", c["accent"])],
            arrowcolor=[("active", c["accent"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", c["panel"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["accent_text"])
        self.root.option_add("*TCombobox*Listbox.font", (self.family, 10))

        style.configure("TSpinbox", fieldbackground=c["input"], foreground=c["text"],
                        bordercolor=c["border"], arrowcolor=c["muted"], padding=(4, 4))
        style.map("TSpinbox", bordercolor=[("focus", c["accent"])])

        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"], focuscolor=c["bg"])
        style.map("TCheckbutton", background=[("active", c["bg"])], foreground=[("disabled", c["muted"])])
        style.configure("Card.TCheckbutton", background=c["panel"], foreground=c["text"], focuscolor=c["panel"])
        style.map("Card.TCheckbutton", background=[("active", c["panel"])])
        style.configure("TRadiobutton", background=c["bg"], foreground=c["text"])
        style.map("TRadiobutton", background=[("active", c["bg"])])
        style.configure("Card.TRadiobutton", background=c["panel"], foreground=c["text"], focuscolor=c["panel"])
        style.map("Card.TRadiobutton", background=[("active", c["panel"])])

        # -- прогресс -----------------------------------------------------
        style.configure(
            "TProgressbar",
            background=c["accent"],
            troughcolor=c["panel_alt"],
            bordercolor=c["panel_alt"],
            lightcolor=c["accent"],
            darkcolor=c["accent"],
            thickness=8,
        )

        # -- таблицы ------------------------------------------------------
        style.configure(
            "Treeview",
            background=c["panel"],
            fieldbackground=c["panel"],
            foreground=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            rowheight=28,
            relief="flat",
            indent=0,
        )
        style.map(
            "Treeview",
            background=[("selected", c["accent"])],
            foreground=[("selected", c["accent_text"])],
        )
        style.configure(
            "Treeview.Heading",
            background=c["panel_alt"],
            foreground=c["muted"],
            relief="flat",
            padding=(8, 6),
            font=(self.family, 9, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", c["hover"])])

        # -- прочее -------------------------------------------------------
        style.configure("TScrollbar", background=c["panel_alt"], troughcolor=c["bg"],
                        bordercolor=c["bg"], arrowcolor=c["muted"], relief="flat")
        style.map("TScrollbar", background=[("active", c["hover"])])
        style.configure("TSeparator", background=c["border"])
        style.configure("TSizegrip", background=c["bg"])

        style.configure("TNotebook", background=c["bg"], bordercolor=c["bg"],
                        lightcolor=c["bg"], darkcolor=c["bg"], tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab", background=c["panel_alt"], foreground=c["muted"],
                        padding=(16, 8), borderwidth=0)
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["panel"]), ("active", c["hover"])],
            foreground=[("selected", c["accent"])],
        )

    # ------------------------------------------------------------------
    def color(self, key: str) -> str:
        return self.colors.get(key, "#000000")
