"""Main-window chrome baseline (UI visual pass phase 1).

**Control stack (O1):** Interactive chrome on the main window prefers **ttk**
(Button, Entry, Scrollbar, Notebook, Treeview, Combobox, Progressbar, Scale,
Separator) so strips do not mix Motif-like classic controls with themed ones.

**Frame language (O4):** Layout regions use flat frames (no stacked sunken
wells). Hairline ``ttk.Separator`` marks toolbar / body / bottom and the
sidebar split.

**Still classic tk (documented exceptions):**

- ``Menu`` (native menubar / context menus)
- ``Text`` / ``Listbox`` (no full ttk equivalent we rely on)
- ``Label`` for ``PhotoImage`` (device graphic, album thumbs live on Treeview)
- ``_HoverTip`` (custom overrideredirect panel)
- Dialogs in ``dialogs.py`` — phase 1 does not rewrite modal shells

Platform theme polish (Aqua / Breeze / Adwaita) is phase 4; this module only
establishes a single stack and flat chrome. See ``docs/ui-visual-pass.md``.
"""

from __future__ import annotations

import logging
import sys
from tkinter import Frame, ttk

logger = logging.getLogger(__name__)


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
) -> ttk.Style:
    """Apply main-window style baseline; return the ``ttk.Style`` instance."""
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

    if tree_rowheight is not None:
        try:
            style.configure("Treeview", rowheight=int(tree_rowheight))
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
