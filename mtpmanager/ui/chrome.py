"""Main-window chrome + control grammar (UI visual pass phases 1–4a).

**Control stack (O1):** Interactive chrome prefers **ttk** (Button, Entry,
Scrollbar, Notebook, Treeview, Combobox, Progressbar, Scale, Separator).

**Frame language (O4):** Flat frames + hairline separators.

**Tree row heights (O11):** ``Thumb.Treeview`` vs ``Compact.Treeview``.

**Control grammar (O5–O7, phase 3):** see glyph constants and
:func:`make_ttk_scale` / :func:`time_of_day_row`.

**macOS blend (phase 4a):** secondary dialog text uses system label colors
when available (:func:`secondary_label_kwargs`); reveal wording is
platform-specific (:func:`reveal_in_file_manager_label`).

**Still classic tk (documented exceptions):**

- ``Menu``; ``Text`` / ``Listbox`` (dialogs); ``Label`` + ``PhotoImage``;
  ``_HoverTip``; many dialog ``Button``/``Checkbutton`` shells.

See ``docs/ui-visual-pass.md``.
"""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from tkinter import DoubleVar, Frame, StringVar, ttk

logger = logging.getLogger(__name__)

# Default row heights when the window does not pass explicit values.
DEFAULT_COMPACT_TREE_ROWHEIGHT = 28
DEFAULT_THUMB_TREE_ROWHEIGHT = 52

STYLE_TREE_COMPACT = "Compact.Treeview"
STYLE_TREE_THUMB = "Thumb.Treeview"
STYLE_BTN_COMPACT = "Compact.TButton"
STYLE_BTN_TOOL = "Tool.TButton"

# --- O5: compact glyphs / short labels (ASCII-first for font portability) ---
GLYPH_ADD = "+"
GLYPH_REMOVE = "-"
GLYPH_DISMISS = "x"
LABEL_MOVE_UP = "Up"
LABEL_MOVE_DOWN = "Dn"
LABEL_REFRESH = "Refresh"


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

    try:
        style.configure(STYLE_BTN_COMPACT, padding=(4, 2))
        style.configure(STYLE_BTN_TOOL, padding=(6, 2))
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


def snap_scale_value(raw: float, *, from_: float, to: float, resolution: float) -> float:
    """Clamp *raw* to [from_, to] and snap to *resolution* steps."""
    lo = float(from_)
    hi = float(to)
    if hi < lo:
        lo, hi = hi, lo
    v = max(lo, min(hi, float(raw)))
    res = float(resolution)
    if res <= 0:
        return v
    steps = round((v - lo) / res)
    snapped = lo + steps * res
    # Avoid float dust (e.g. 1.999999).
    if res >= 1 and float(res).is_integer():
        return float(int(round(snapped)))
    # Quantize to resolution decimals when sensible.
    decimals = max(0, min(6, -int(math.floor(math.log10(res))) if res < 1 else 0))
    if decimals:
        return round(snapped, decimals)
    return snapped


def make_ttk_scale(
    parent,
    *,
    from_: float,
    to: float,
    value: float | None = None,
    variable: DoubleVar | None = None,
    length: int = 260,
    resolution: float = 1.0,
    command: Callable[[str], None] | None = None,
    orient: str = "horizontal",
) -> tuple[ttk.Scale, DoubleVar]:
    """Create a ``ttk.Scale`` with optional step snapping (phase 3 / O7).

    Classic ``tk.Scale`` is not used in the app UI. *command* receives the
    string value (ttk convention); the variable is always a ``DoubleVar``.
    """
    var = variable if variable is not None else DoubleVar()
    initial = float(value) if value is not None else float(var.get() or from_)
    initial = snap_scale_value(
        initial, from_=from_, to=to, resolution=resolution
    )
    var.set(initial)

    def _on_slide(raw: str) -> None:
        try:
            snapped = snap_scale_value(
                float(raw), from_=from_, to=to, resolution=resolution
            )
        except (TypeError, ValueError):
            snapped = float(from_)
        if abs(float(var.get()) - snapped) > 1e-9:
            var.set(snapped)
        if command is not None:
            command(str(snapped))

    scale = ttk.Scale(
        parent,
        from_=float(from_),
        to=float(to),
        orient=orient,
        variable=var,
        length=int(length),
        command=_on_slide,
    )
    return scale, var


def time_of_day_row(
    parent,
    *,
    initial_hhmm: str,
    hour_values: list[str] | None = None,
    minute_values: list[str] | None = None,
) -> tuple[Frame, Callable[[], str]]:
    """Hour + minute + AM/PM comboboxes; getter → normalized 24h ``HH:MM``.

    Replaces the custom ▲/▼ spinner columns (phase 3 / O6).
    """
    from mtpmanager.app.podcast_schedule import components_to_hhmm, hhmm_to_12h

    hour0, minute0, ampm0 = hhmm_to_12h(initial_hhmm)
    row = Frame(parent)
    hours = hour_values or [str(h) for h in range(1, 13)]
    minutes = minute_values or [f"{m:02d}" for m in range(60)]
    hour_var = StringVar(value=str(hour0))
    min_var = StringVar(value=f"{minute0:02d}")
    ampm_var = StringVar(value=ampm0 if ampm0 in ("AM", "PM") else "AM")

    hour_cb = ttk.Combobox(
        row,
        textvariable=hour_var,
        values=hours,
        state="readonly",
        width=3,
    )
    hour_cb.pack(side="left")
    ttk.Label(row, text=":").pack(side="left", padx=2)
    min_cb = ttk.Combobox(
        row,
        textvariable=min_var,
        values=minutes,
        state="readonly",
        width=3,
    )
    min_cb.pack(side="left")
    ampm_cb = ttk.Combobox(
        row,
        textvariable=ampm_var,
        values=("AM", "PM"),
        state="readonly",
        width=4,
    )
    ampm_cb.pack(side="left", padx=(8, 0))

    def get_hhmm() -> str:
        try:
            h = int(str(hour_var.get()).strip() or "12")
        except ValueError:
            h = 12
        try:
            m = int(str(min_var.get()).strip() or "0")
        except ValueError:
            m = 0
        return components_to_hhmm(h, m, ampm_var.get())

    return row, get_hhmm


def int_spinbox(
    parent,
    *,
    from_: int,
    to: int,
    textvariable: StringVar,
    width: int = 4,
) -> ttk.Spinbox | ttk.Entry:
    """``ttk.Spinbox`` when available; otherwise a plain ``ttk.Entry``."""
    try:
        sp = ttk.Spinbox(
            parent,
            from_=int(from_),
            to=int(to),
            textvariable=textvariable,
            width=int(width),
        )
        return sp
    except Exception:
        logger.debug("ttk.Spinbox unavailable; using Entry", exc_info=True)
        return ttk.Entry(parent, textvariable=textvariable, width=int(width))


# --- Phase 4a: platform wording + dark-safe secondary text -----------------


def secondary_label_fg() -> str | None:
    """Foreground for secondary/helper prose, or None for theme default.

    On macOS Aqua, ``systemSecondaryLabelColor`` tracks light/dark appearance.
    Hard-coded ``#333``/``#666`` break dark mode (O12). Elsewhere omit *fg*
    so classic Labels inherit the system text color.
    """
    if sys.platform == "darwin":
        return "systemSecondaryLabelColor"
    return None


def secondary_label_kwargs() -> dict[str, str]:
    """Kwargs to splat into ``Label(...)`` for secondary prose."""
    fg = secondary_label_fg()
    return {"fg": fg} if fg else {}


def reveal_in_file_manager_label(*, download: bool = False) -> str:
    """User-visible “reveal path” action (O13 — no Finder wording on Linux)."""
    if download:
        if sys.platform == "darwin":
            return "Reveal Download in Finder"
        if sys.platform == "win32":
            return "Reveal Download in Explorer"
        return "Show Download in File Manager"
    if sys.platform == "darwin":
        return "Reveal in Finder"
    if sys.platform == "win32":
        return "Reveal in Explorer"
    return "Show in File Manager"


def monospace_ui_font(size: int = 11) -> tuple[str, int]:
    """Preferred monospaced UI font: Menlo on macOS, else TkFixedFont family."""
    if sys.platform == "darwin":
        return ("Menlo", int(size))
    return ("TkFixedFont", int(size))
