"""Общие виджеты и мелкие помощники для интерфейса."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Iterable, Optional, Sequence

from .theme import Theme


# ---------------------------------------------------------------------------
# Диалоги
# ---------------------------------------------------------------------------


def show_error(parent, message: str, title: str = "Ошибка") -> None:
    messagebox.showerror(title, str(message), parent=parent)


def show_info(parent, message: str, title: str = "PenguinCraft") -> None:
    messagebox.showinfo(title, str(message), parent=parent)


def show_warning(parent, message: str, title: str = "Внимание") -> None:
    messagebox.showwarning(title, str(message), parent=parent)


def ask_yes_no(parent, message: str, title: str = "Подтверждение") -> bool:
    return bool(messagebox.askyesno(title, str(message), parent=parent))


def ask_string(parent, prompt: str, title: str = "PenguinCraft", initial: str = "") -> Optional[str]:
    from tkinter import simpledialog

    return simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent)


# ---------------------------------------------------------------------------
# Геометрия
# ---------------------------------------------------------------------------


def center(window: tk.Misc, width: int, height: int) -> None:
    """Ставит окно по центру экрана и задаёт размер."""
    try:
        window.update_idletasks()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
    except tk.TclError:  # pragma: no cover
        return
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    window.geometry(f"{width}x{height}+{x}+{y}")


# ---------------------------------------------------------------------------
# Прокручиваемая область
# ---------------------------------------------------------------------------


class ScrollFrame(ttk.Frame):
    """Прокручиваемый контейнер.  Содержимое складывайте в ``.body``."""

    def __init__(self, master, theme: Theme, *, padding: int | tuple = 0, style: str = "TFrame", **kwargs):
        super().__init__(master, style=style, **kwargs)
        self.theme = theme
        colors = theme.colors
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=colors["bg"])
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body = ttk.Frame(self.canvas, style=style, padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda _e: self._wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._wheel(False))

    def _on_body(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _wheel(self, enable: bool) -> None:
        if enable:
            self.canvas.bind_all("<MouseWheel>", self._scroll)
            self.canvas.bind_all("<Button-4>", self._scroll)
            self.canvas.bind_all("<Button-5>", self._scroll)
        else:
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")

    def _scroll(self, event) -> None:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -int(event.delta / 120) if event.delta else 0
        if delta:
            self.canvas.yview_scroll(delta, "units")


# ---------------------------------------------------------------------------
# Карточка
# ---------------------------------------------------------------------------


class Card(ttk.Frame):
    """Панель с заголовком и содержимым в ``.body``."""

    def __init__(self, master, theme: Theme, title: str = "", *, padding: int = 16, **kwargs):
        super().__init__(master, style="Card.TFrame", padding=padding, **kwargs)
        self.theme = theme
        if title:
            header = ttk.Frame(self, style="Card.TFrame")
            header.pack(fill="x", anchor="w")
            ttk.Label(header, text=title, style="CardTitle.TLabel").pack(side="left")
            self.header = header
        else:
            self.header = None
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.pack(fill="both", expand=True, pady=(10, 0) if title else 0)


# ---------------------------------------------------------------------------
# Форма «подпись → поле»
# ---------------------------------------------------------------------------


class Form(ttk.Frame):
    """Сетка из подписей и полей."""

    def __init__(self, master, theme: Theme, *, label_width: int = 26, style: str = "Card.TFrame"):
        super().__init__(master, style=style)
        self.theme = theme
        self.label_width = label_width
        self._row = 0
        self.columnconfigure(1, weight=1)

    def add(self, label: str, widget: tk.Widget, hint: str = "") -> tk.Widget:
        frame = ttk.Frame(self, style="Card.TFrame")
        frame.grid(row=self._row, column=0, sticky="nw", padx=(0, 12), pady=5)
        ttk.Label(frame, text=label, style="CardMuted.TLabel", width=self.label_width, anchor="w").pack(anchor="w")
        widget.grid(row=self._row, column=1, sticky="ew", pady=5)
        self._row += 1
        if hint:
            ttk.Label(self, text=hint, style="CardMuted.TLabel", wraplength=520, justify="left").grid(
                row=self._row, column=1, sticky="w", pady=(0, 6)
            )
            self._row += 1
        return widget

    def add_row(self, widget: tk.Widget) -> tk.Widget:
        widget.grid(row=self._row, column=0, columnspan=2, sticky="ew", pady=5)
        self._row += 1
        return widget

    def separator(self) -> None:
        ttk.Separator(self, orient="horizontal").grid(
            row=self._row, column=0, columnspan=2, sticky="ew", pady=8
        )
        self._row += 1


# ---------------------------------------------------------------------------
# Таблица
# ---------------------------------------------------------------------------


class Table(ttk.Frame):
    """Обёртка над :class:`ttk.Treeview` с прокруткой и удобным API."""

    def __init__(
        self,
        master,
        theme: Theme,
        columns: Sequence[tuple],
        *,
        height: int = 12,
        selectmode: str = "browse",
        on_select: Optional[Callable[[str], None]] = None,
        on_activate: Optional[Callable[[str], None]] = None,
        icon_width: int = 0,
    ):
        super().__init__(master, style="TFrame")
        self.theme = theme
        self._on_select = on_select
        self._on_activate = on_activate
        keys = [item[0] for item in columns]
        # Картинки в ttk.Treeview рисуются только в колонке дерева (#0),
        # поэтому для списков с иконками её показываем узкой колонкой.
        # Tk резервирует слева ~18 px под «раскрывашку», это учтено в
        # ширине, которую передают через *icon_width*.
        show = ("tree", "headings") if icon_width else "headings"
        self.tree = ttk.Treeview(self, columns=keys, show=show, height=height, selectmode=selectmode)
        if icon_width:
            self.tree.heading("#0", text="")
            self.tree.column("#0", width=icon_width, minwidth=icon_width, stretch=False, anchor="center")
        for key, title, width, *rest in columns:
            anchor = rest[0] if rest else "w"
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == keys[-1]))
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("alt", background=theme.colors["tree_alt"])
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        self.tree.bind("<Double-1>", self._activated)

    # ------------------------------------------------------------------
    def _selected(self, _event=None) -> None:
        if self._on_select is not None:
            self._on_select(self.selected())

    def _activated(self, _event=None) -> None:
        if self._on_activate is not None:
            self._on_activate(self.selected())

    def selected(self) -> str:
        selection = self.tree.selection()
        return selection[0] if selection else ""

    def clear(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

    def add(self, iid: str, values: Sequence, *, tags: Iterable[str] = (), image=None) -> str:
        kwargs = {}
        if image is not None:
            kwargs["image"] = image
        return self.tree.insert("", "end", iid=iid, values=list(values), tags=tuple(tags), **kwargs)

    def set_values(self, iid: str, values: Sequence) -> None:
        if self.tree.exists(iid):
            self.tree.item(iid, values=list(values))

    def select_first(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    def count(self) -> int:
        return len(self.tree.get_children())


# ---------------------------------------------------------------------------
# Многострочное поле вывода
# ---------------------------------------------------------------------------


class LogView(ttk.Frame):
    """Текстовое поле для логов с автопрокруткой и ограничением строк."""

    def __init__(self, master, theme: Theme, *, max_lines: int = 4000, **kwargs):
        super().__init__(master, style="TFrame")
        self.theme = theme
        self.max_lines = max_lines
        self.text = tk.Text(
            self,
            wrap="none",
            background=theme.colors["input"],
            foreground=theme.colors["text"],
            insertbackground=theme.colors["text"],
            selectbackground=theme.colors["accent"],
            selectforeground=theme.colors["accent_text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=theme.colors["border"],
            font=(theme.mono, 10),
            padx=8,
            pady=6,
        )
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.configure(state="disabled")
        self._autoscroll = True
        self.text.bind("<KeyRelease>", self._track_autoscroll)

    def _track_autoscroll(self, _event=None) -> None:
        try:
            self._autoscroll = self.text.yview()[1] >= 0.999
        except tk.TclError:
            self._autoscroll = True

    def append(self, line: str) -> None:
        self._append(line)

    def append_many(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._append(line)

    def _append(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        # Обрезаем слишком длинный буфер.
        try:
            count = int(self.text.index("end-1c").split(".")[0])
        except (ValueError, tk.TclError):
            count = 0
        if count > self.max_lines:
            self.text.delete("1.0", f"{count - self.max_lines}.0")
        self.text.configure(state="disabled")
        if self._autoscroll:
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def get_all(self) -> str:
        return self.text.get("1.0", "end-1c")


# ---------------------------------------------------------------------------
# Плитка-«чип» со значением
# ---------------------------------------------------------------------------


class StatTile(ttk.Frame):
    """Небольшая карточка «подпись + значение»."""

    def __init__(self, master, theme: Theme, title: str, value: str = "—"):
        super().__init__(master, style="Card.TFrame", padding=(14, 10))
        self.theme = theme
        ttk.Label(self, text=title, style="CardMuted.TLabel").pack(anchor="w")
        self.value_label = ttk.Label(self, text=value, style="Stat.TLabel")
        self.value_label.pack(anchor="w", pady=(2, 0))

    def set_value(self, value: str) -> None:
        self.value_label.configure(text=value)
