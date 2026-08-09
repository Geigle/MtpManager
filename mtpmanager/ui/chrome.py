"""Main-window chrome baseline (UI visual pass phases 1–2).

**Control stack (O1):** Interactive chrome on the main window prefers **ttk**
(Button, Entry, Scrollbar, Notebook, Treeview, Combobox, Progressbar, Scale,
Separator) so strips do not mix Motif-like classic controls with themed ones.

**Frame language (O4):** Layout regions use flat frames (no stacked sunken
wells). Hairline ``ttk.Separator`` marks toolbar / body / bottom and the
sidebar split.

**Tree row heights (O11):** Named styles so album-art trees can be tall without
forcing empty bulk on playlist / episode / subscription lists:

- ``Thumb.Treeview`` — library/device rows that show album art
- ``Compact.Treeview`` — flat lists (playlists, episodes, shows)
- default ``Treeview`` — same as Compact unless a caller sets ``tree_rowheight``

**Still classic tk (documented exceptions):**

- ``Menu`` (native menubar / context menus)
- ``Text`` / ``Listbox`` (dialogs and legacy; main Podcasts shows are Treeview)
- ``Label`` for ``PhotoImage`` (device graphic, album thumbs live on Treeview)
- ``_HoverTip`` (custom overrideredirect panel)
- Dialogs in ``dialogs.py`` — not rewritten in phase 1–2

Platform theme polish (Aqua / Breeze / Adwaita) is phase 4. See
``docs/ui-visual-pass.md``.
"""

from __future__ import annotations

import logging
import sys
from tkinter import Frame, ttk

logger = logging.getLogger(__name__)

# Default row heights when the window does not pass explicit values.
DEFAULT_COMPACT_TREE_ROWHEIGHT = 28
DEFAULT_THUMB_TREE_ROWHEIGHT = 52

STYLE_TREE_COMPACT = "Compact.Treeview"
STYLE_TREE_THUMB = "Thumb.Treeview"


def preferred_ttk_theme() -> str | None:
    """Theme to request, or None to keep Tk's platform default.

    macOS: leave **aqua**. Linux: do not force a theme yet (phase 4b/4c);
    default/clam choice waits for a real Plasma/GNOME pass.
    """
    if sys.platform == "darwin":
        return None
    return None


def apply_chrome_baseline(
    root,
    *,
    tree_rowheight: int | None = None,
    compact_tree_rowheight: int | None = None,
    thumb_tree_rowheight: int | None = None,
) -> ttk.Style:
    """Apply main-window style baseline; return the ``ttk.Style`` instance.

    *tree_rowheight* — legacy alias for the default ``Treeview`` style (also
    used as thumb height when *thumb_tree_rowheight* is omitted).
    """
    style = ttk.Style(root)
    want = preferred_ttk_theme()
    if want is not None:
        try:
            names = set(style.theme_names())
            if want in names:
                style.theme_use(want)
        except Exception:
            logger.debug("ttk theme_use(%r) failed", want, exc_info=True)

    # Slightly tighter padding for tool/glyph buttons (width still per widget).
    try:
        style.configure("Compact.TButton", padding=(4, 2))
        style.configure("Tool.TButton", padding=(6, 2))
    except Exception:
        logger.debug("ttk button style configure failed", exc_info=True)

    compact = int(
        compact_tree_rowheight
        if compact_tree_rowheight is not None
        else DEFAULT_COMPACT_TREE_ROWHEIGHT
    )
    thumb = int(
        thumb_tree_rowheight
        if thumb_tree_rowheight is not None
        else (
            tree_rowheight
            if tree_rowheight is not None
            else DEFAULT_THUMB_TREE_ROWHEIGHT
        )
    )
    # Default Treeview = compact so unnamed trees stay dense.
    default_h = int(tree_rowheight) if tree_rowheight is not None else compact
    try:
        style.configure("Treeview", rowheight=default_h)
        style.configure(STYLE_TREE_COMPACT, rowheight=compact)
        style.configure(STYLE_TREE_THUMB, rowheight=thumb)
    except Exception:
        logger.debug("Treeview rowheight configure failed", exc_info=True)

    return style


def flat_frame(parent, **kwargs) -> Frame:
    """Classic Frame with no 3D relief (layout container only)."""
    kwargs.setdefault("borderwidth", 0)
    kwargs.setdefault("relief", "flat")
    kwargs.setdefault("highlightthickness", 0)
    return Frame(parent, **kwargs)


def h_separator(parent) -> ttk.Separator:
    return ttk.Separator(parent, orient="horizontal")


def v_separator(parent) -> ttk.Separator:
    return ttk.Separator(parent, orient="vertical")
