"""Modal dialogs for device admin (no persistent main-window entry field)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tkinter import (
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    Listbox,
    Menu,
    Radiobutton,
    Scrollbar,
    StringVar,
    Toplevel,
    Y,
    messagebox,
    simpledialog,
)
from tkinter import ttk

from mtpmanager.domain.audio_encode import (
    BIT_DEPTH_CHOICES,
    SAMPLE_RATE_CHOICES,
    AudioEncodeSettings,
    bitrate_choices_for_format,
    clamp_settings_for_format,
    format_display_name,
    formats_allowed,
    get_preset,
    presets_for_format,
    resolve_settings,
)
from mtpmanager.domain.device_profile import DeviceVideoOptions, VideoEncodePreset
from mtpmanager.domain.models import DeviceInfo
from mtpmanager.app.podcast_schedule import components_to_hhmm, hhmm_to_12h
from mtpmanager.infra.app_config import (
    ALL_DAY_KEYS,
    DEFAULT_PODCAST_SCHEDULE_TIME,
    MAX_PODCAST_NEW_PER_SHOW,
    VALID_SEND_FORMATS,
    WEEKDAY_KEYS,
    default_audiobook_audio_encode_settings,
    default_podcast_audio_encode_settings,
    normalize_max_new_per_show,
    normalize_schedule_days,
    normalize_schedule_time,
)
from mtpmanager.infra.remote_naming import (
    DEFAULT_TV_FOLDER_ID,
    DEFAULT_VIDEO_FOLDER_ID,
    ZEN_VISION_M_FOLDER_IDS,
)
from mtpmanager.ui.formatting import folder_line


def _video_folder_radio_label(kind: str, folder_id: int, name: str) -> str:
    """Label like ``Video  (folder 108 — Video)`` for destination radios."""
    label = (name or "").strip() or kind
    return f"{kind}  (folder {int(folder_id)} — {label})"


def ask_text(
    parent,
    *,
    title: str,
    prompt: str,
    initialvalue: str = "",
) -> str | None:
    """Return stripped text, or None if cancelled / empty after strip."""
    raw = simpledialog.askstring(
        title,
        prompt,
        parent=parent,
        initialvalue=initialvalue,
    )
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def show_device_info_dialog(
    parent,
    info: DeviceInfo,
    *,
    apply_name: Callable[[str], None],
) -> None:
    """Modal Device Info dialog with editable name.

    On Close: if the name field differs from the original and is non-empty,
    call *apply_name(new_name)*. On rename failure, keep the dialog open.
    """
    original = (info.name or "").strip()
    dlg = Toplevel(parent)
    dlg.title("Device Info")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    # Name (editable)
    row_name = Frame(body)
    row_name.pack(fill="x", pady=2)
    Label(row_name, text="Name:", width=14, anchor="w").pack(side=LEFT)
    name_entry = Entry(row_name, width=40)
    name_entry.pack(side=LEFT, fill="x", expand=True)
    name_entry.insert(0, original)
    name_entry.focus_set()

    used_mb = (info.used or 0) / 1_000_000
    total_mb = (info.total or 0) / 1_000_000
    readonly_rows = (
        ("Serial", info.serial or ""),
        ("Manufacturer", info.manufacturer or ""),
        ("Model", info.model or ""),
        ("Version", info.version or ""),
        ("Battery", "" if info.battery is None else str(info.battery)),
        ("Used", f"{used_mb:.2f} / {total_mb:.2f} MB"),
        ("Used %", f"{info.used_percent:.2f}"),
        ("Free", str(info.free)),
    )
    for label, value in readonly_rows:
        row = Frame(body)
        row.pack(fill="x", pady=2)
        Label(row, text=f"{label}:", width=14, anchor="w").pack(side=LEFT)
        Label(row, text=value, anchor="w").pack(side=LEFT, fill="x", expand=True)

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(12, 0))

    def try_close() -> None:
        new_name = name_entry.get().strip()
        if new_name and new_name != original:
            try:
                apply_name(new_name)
            except Exception as e:
                messagebox.showerror("Device Name", str(e), parent=dlg)
                return
        dlg.destroy()

    Button(btn_row, text="Close", width=10, command=try_close).pack(side=RIGHT)
    dlg.protocol("WM_DELETE_WINDOW", try_close)

    dlg.grab_set()
    dlg.update_idletasks()
    # Center roughly over parent
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - dlg.winfo_width()) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - dlg.winfo_height()) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)


@dataclass(frozen=True)
class ConfigDialogResult:
    """Values saved from Config → Config…"""

    send_format: str
    audio_encode: AudioEncodeSettings
    show_broken_video_presets: bool = False


def show_config_dialog(
    parent,
    *,
    send_format: str,
    audio_encode: AudioEncodeSettings | None = None,
    show_broken_video_presets: bool = False,
    allowed_send_formats: frozenset[str] | None = None,
    profile_display_name: str | None = None,
) -> ConfigDialogResult | None:
    """Edit app preferences. Returns result on Save, or None if cancelled.

    Transfer mode (Stable vs PyMTP) is a separate Config menu checkbutton.

    Layout is a compact notebook (Presets / Advanced / Video) so the dialog
    stays usable on shorter displays without stacking every control.

    *allowed_send_formats*: device-profile restriction (None = unrestricted).
    """
    from tkinter import Scale, ttk

    allowed_tuple = formats_allowed(allowed_send_formats)
    initial_settings = resolve_settings(
        settings=audio_encode,
        send_format=send_format,
        allowed_formats=allowed_send_formats,
    )
    # UI format key: map aac under m4a when both not listed separately.
    ui_fmt = initial_settings.normalized_format()
    if ui_fmt == "aac" and "m4a" in allowed_tuple and "aac" not in allowed_tuple:
        ui_fmt = "m4a"
    if ui_fmt not in allowed_tuple:
        ui_fmt = allowed_tuple[0] if allowed_tuple else "mp3"

    start_custom = bool(
        initial_settings.preset_id == "custom"
        or (
            initial_settings.preset_id
            and get_preset(initial_settings.preset_id) is None
        )
    )

    dlg = Toplevel(parent)
    dlg.title("Config")
    dlg.transient(parent)
    dlg.resizable(True, True)

    outer = Frame(dlg, padx=12, pady=10)
    outer.pack(fill=BOTH, expand=True)
    outer.rowconfigure(0, weight=1)
    outer.columnconfigure(0, weight=1)

    nb = ttk.Notebook(outer)
    nb.grid(row=0, column=0, sticky="nsew")

    tab_presets = Frame(nb, padx=10, pady=8)
    tab_advanced = Frame(nb, padx=10, pady=8)
    tab_video = Frame(nb, padx=10, pady=8)
    nb.add(tab_presets, text="Audio presets")
    nb.add(tab_advanced, text="Advanced")
    nb.add(tab_video, text="Video")

    # ---- Tab: Audio presets ----
    if allowed_send_formats is not None:
        names = ", ".join(format_display_name(f) for f in allowed_tuple)
        who = profile_display_name or "this device"
        restrict_note = f"Formats limited by {who}: {names}."
    else:
        restrict_note = "No device profile restriction (all encode formats)."

    Label(
        tab_presets,
        text=(
            "When conversion is needed, encode with the selected preset. "
            "Tracks already in a device-supported format are sent as-is.\n"
            + restrict_note
        ),
        justify=LEFT,
        wraplength=640,
        fg="#333",
    ).pack(anchor="w", pady=(0, 6))

    fmt_row = Frame(tab_presets)
    fmt_row.pack(fill="x", pady=(0, 4))
    Label(fmt_row, text="Output format:").pack(side=LEFT)
    fmt_keys = list(allowed_tuple)
    fmt_labels = [format_display_name(f) for f in fmt_keys]
    label_to_key = dict(zip(fmt_labels, fmt_keys))
    key_to_label = dict(zip(fmt_keys, fmt_labels))
    fmt_var = StringVar(value=key_to_label.get(ui_fmt, fmt_labels[0]))
    fmt_combo = ttk.Combobox(
        fmt_row,
        textvariable=fmt_var,
        values=fmt_labels,
        state="readonly",
        width=16,
    )
    fmt_combo.pack(side=LEFT, padx=(8, 0))

    Label(tab_presets, text="Quality (low → high):").pack(anchor="w", pady=(4, 0))

    preset_list_frame = Frame(tab_presets)
    preset_list_frame.pack(fill=BOTH, expand=True, pady=(4, 2))
    preset_scroll = Scrollbar(preset_list_frame)
    preset_scroll.pack(side=RIGHT, fill=Y)
    preset_list = Listbox(
        preset_list_frame,
        height=8,
        width=62,
        exportselection=False,
        yscrollcommand=preset_scroll.set,
    )
    preset_list.pack(side=LEFT, fill=BOTH, expand=True)
    preset_scroll.config(command=preset_list.yview)

    preset_blurb = Label(
        tab_presets, text="", justify=LEFT, wraplength=640, fg="#444", height=2
    )
    preset_blurb.pack(anchor="w", fill="x", pady=(2, 0))

    Label(
        tab_presets,
        text=(
            "Transfer engine: Config → Stable Mode "
            "(off = PyMTP, on = mtp-sendtr)."
        ),
        justify=LEFT,
        wraplength=640,
        fg="#666",
    ).pack(anchor="w", pady=(6, 0))

    shown_presets: list = []

    # ---- Tab: Advanced ----
    Label(
        tab_advanced,
        text=(
            "Granular encode options. Editing any control marks the recipe "
            "as custom (overrides the Presets list until you pick a preset again)."
        ),
        justify=LEFT,
        wraplength=640,
        fg="#333",
    ).pack(anchor="w", pady=(0, 8))

    adv = Frame(tab_advanced)
    adv.pack(fill=BOTH, expand=True)

    Label(adv, text="Rate control:", anchor="w").grid(row=0, column=0, sticky="w")
    rc_var = StringVar(value=initial_settings.rate_control)
    rc_row = Frame(adv)
    rc_row.grid(row=0, column=1, sticky="w", padx=(8, 0))
    for text, val in (
        ("CBR", "cbr"),
        ("VBR", "vbr"),
        ("ABR", "abr"),
        ("Lossless", "lossless"),
        ("PCM", "pcm"),
    ):
        Radiobutton(rc_row, text=text, variable=rc_var, value=val).pack(
            side=LEFT, padx=(0, 6)
        )

    Label(adv, text="Bitrate (kbps):", anchor="w").grid(
        row=1, column=0, sticky="w", pady=(8, 0)
    )
    br_var = StringVar(value=str(initial_settings.bitrate_kbps or 192))
    br_combo = ttk.Combobox(
        adv,
        textvariable=br_var,
        values=[str(b) for b in bitrate_choices_for_format(ui_fmt)],
        width=10,
    )
    br_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

    Label(adv, text="VBR quality:", anchor="w").grid(
        row=2, column=0, sticky="w", pady=(8, 0)
    )
    vbr_frame = Frame(adv)
    vbr_frame.grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(8, 0))
    vbr_var = StringVar(
        value=str(
            int(initial_settings.vbr_quality)
            if initial_settings.vbr_quality is not None
            and float(initial_settings.vbr_quality).is_integer()
            else (
                f"{initial_settings.vbr_quality:g}"
                if initial_settings.vbr_quality is not None
                else "2"
            )
        )
    )
    vbr_scale = Scale(
        vbr_frame,
        from_=0,
        to=10,
        orient="horizontal",
        length=260,
        resolution=0.5,
        showvalue=0,
        command=lambda v: vbr_var.set(
            str(int(float(v))) if float(v) == int(float(v)) else f"{float(v):g}"
        ),
    )
    try:
        vbr_scale.set(float(initial_settings.vbr_quality or 2))
    except Exception:
        vbr_scale.set(2)
    vbr_scale.pack(side=LEFT)
    Label(vbr_frame, textvariable=vbr_var, width=4).pack(side=LEFT, padx=(4, 0))
    Label(
        vbr_frame,
        text="MP3 0=best…9 · Vorbis 0–10",
        fg="#666",
    ).pack(side=LEFT, padx=(6, 0))

    Label(adv, text="Sample rate:", anchor="w").grid(
        row=3, column=0, sticky="w", pady=(8, 0)
    )
    sr_choices = ["Source (keep)"] + [str(r) for r in SAMPLE_RATE_CHOICES]
    sr_var = StringVar(
        value=(
            str(initial_settings.sample_rate)
            if initial_settings.sample_rate
            else "Source (keep)"
        )
    )
    sr_combo = ttk.Combobox(
        adv, textvariable=sr_var, values=sr_choices, state="readonly", width=14
    )
    sr_combo.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

    Label(adv, text="Channels:", anchor="w").grid(
        row=4, column=0, sticky="w", pady=(8, 0)
    )
    ch_var = StringVar(
        value=(
            "Mono"
            if initial_settings.channels == 1
            else "Stereo"
            if initial_settings.channels == 2
            else "Source (keep)"
        )
    )
    ch_combo = ttk.Combobox(
        adv,
        textvariable=ch_var,
        values=("Source (keep)", "Mono", "Stereo"),
        state="readonly",
        width=14,
    )
    ch_combo.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

    Label(adv, text="Bit depth:", anchor="w").grid(
        row=5, column=0, sticky="w", pady=(8, 0)
    )
    depth_var = StringVar(value=str(initial_settings.bit_depth or 16))
    depth_combo = ttk.Combobox(
        adv,
        textvariable=depth_var,
        values=[str(d) for d in BIT_DEPTH_CHOICES],
        state="readonly",
        width=10,
    )
    depth_combo.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
    Label(adv, text="(PCM / FLAC)", fg="#666").grid(
        row=5, column=2, sticky="w", padx=(4, 0), pady=(8, 0)
    )

    Label(adv, text="FLAC level:", anchor="w").grid(
        row=6, column=0, sticky="w", pady=(8, 0)
    )
    flac_frame = Frame(adv)
    flac_frame.grid(row=6, column=1, sticky="we", padx=(8, 0), pady=(8, 0))
    flac_var = StringVar(
        value=str(
            initial_settings.compression_level
            if initial_settings.compression_level is not None
            else 5
        )
    )
    flac_scale = Scale(
        flac_frame,
        from_=0,
        to=12,
        orient="horizontal",
        length=260,
        resolution=1,
        showvalue=0,
        command=lambda v: flac_var.set(str(int(float(v)))),
    )
    flac_scale.set(
        int(
            initial_settings.compression_level
            if initial_settings.compression_level is not None
            else 5
        )
    )
    flac_scale.pack(side=LEFT)
    Label(flac_frame, textvariable=flac_var, width=3).pack(side=LEFT, padx=(4, 0))
    Label(flac_frame, text="0=fast … 12=smallest", fg="#666").pack(
        side=LEFT, padx=(6, 0)
    )

    # ---- Tab: Video ----
    Label(
        tab_video,
        text="Send Video (device-specific)",
        font=("", 11, "bold"),
        anchor="w",
    ).pack(fill="x", pady=(0, 6))

    broken_var = BooleanVar(value=bool(show_broken_video_presets))
    Checkbutton(
        tab_video,
        text="Show broken video encode presets (experimental)",
        variable=broken_var,
        anchor="w",
        justify=LEFT,
    ).pack(fill="x", pady=(0, 4))
    Label(
        tab_video,
        text=(
            "When enabled, Device → Send Video can offer recipes marked "
            "broken (e.g. ZEN Vision:M WMV · WMA). They are hidden by "
            "default because they do not play reliably."
        ),
        justify=LEFT,
        wraplength=640,
        fg="#333",
    ).pack(anchor="w")

    # ---- Footer: active recipe + buttons ----
    foot = Frame(outer)
    foot.grid(row=1, column=0, sticky="ew", pady=(8, 0))

    use_advanced = {"v": start_custom}
    summary_var = StringVar(value=initial_settings.summary_line())
    source_var = StringVar(
        value="Using: custom advanced" if start_custom else "Using: preset"
    )
    Label(
        foot,
        textvariable=source_var,
        anchor="w",
        fg="#555",
    ).pack(fill="x")
    Label(
        foot,
        textvariable=summary_var,
        justify=LEFT,
        wraplength=640,
        font=("", 10, "italic"),
        anchor="w",
    ).pack(fill="x", pady=(2, 6))

    # ---- state helpers ----
    _suppress = {"n": False}

    def _current_fmt_key() -> str:
        lab = (fmt_var.get() or "").strip()
        return label_to_key.get(lab, fmt_keys[0] if fmt_keys else "mp3")

    def _reload_presets(*, select_id: str | None = None) -> None:
        nonlocal shown_presets
        fmt = _current_fmt_key()
        shown_presets = presets_for_format(fmt)
        if fmt == "m4a" and not shown_presets:
            shown_presets = presets_for_format("aac")
        preset_list.delete(0, END)
        for p in shown_presets:
            preset_list.insert(END, p.display_name)
        idx = 0
        want = select_id or initial_settings.preset_id
        for i, p in enumerate(shown_presets):
            if p.id == want:
                idx = i
                break
        else:
            if shown_presets:
                idx = min(len(shown_presets) - 1, max(0, len(shown_presets) // 2 + 1))
        if shown_presets:
            preset_list.selection_clear(0, END)
            preset_list.selection_set(idx)
            preset_list.see(idx)
            blurb = (
                shown_presets[idx].blurb
                or shown_presets[idx].settings.summary_line()
            )
            preset_blurb.config(text=blurb)
        else:
            preset_blurb.config(text="No presets for this format.")
        br_combo["values"] = [str(b) for b in bitrate_choices_for_format(fmt)]

    def _selected_preset_settings() -> AudioEncodeSettings | None:
        sel = preset_list.curselection()
        if not sel or not shown_presets:
            return None
        i = int(sel[0])
        if 0 <= i < len(shown_presets):
            return shown_presets[i].settings
        return None

    def _read_advanced() -> AudioEncodeSettings:
        fmt = _current_fmt_key()
        rc = (rc_var.get() or "vbr").lower()
        if rc not in ("cbr", "vbr", "abr", "lossless", "pcm"):
            rc = "vbr"
        try:
            br = int(float(br_var.get()))
        except (TypeError, ValueError):
            br = 192
        try:
            vq = float(vbr_var.get())
        except (TypeError, ValueError):
            vq = 2.0
        sr_raw = (sr_var.get() or "").strip()
        if sr_raw.lower().startswith("source") or not sr_raw:
            sr = None
        else:
            try:
                sr = int(sr_raw)
            except ValueError:
                sr = None
        ch_raw = (ch_var.get() or "").strip().lower()
        if ch_raw.startswith("mono"):
            ch = 1
        elif ch_raw.startswith("stereo"):
            ch = 2
        else:
            ch = None
        try:
            depth = int(depth_var.get())
        except (TypeError, ValueError):
            depth = 16
        try:
            comp = int(float(flac_var.get()))
        except (TypeError, ValueError):
            comp = 5
        s = AudioEncodeSettings(
            format=fmt,
            preset_id="custom",
            rate_control=rc,  # type: ignore[arg-type]
            bitrate_kbps=br,
            vbr_quality=vq,
            sample_rate=sr,
            channels=ch,
            bit_depth=depth,
            compression_level=comp,
            label="",
        )
        s = clamp_settings_for_format(s)
        label = s.summary_line()
        return AudioEncodeSettings(
            format=s.format,
            preset_id="custom",
            rate_control=s.rate_control,
            bitrate_kbps=s.bitrate_kbps,
            vbr_quality=s.vbr_quality,
            sample_rate=s.sample_rate,
            channels=s.channels,
            bit_depth=s.bit_depth,
            compression_level=s.compression_level,
            label=f"Custom {label}",
        )

    def _apply_settings_to_advanced(s: AudioEncodeSettings) -> None:
        _suppress["n"] = True
        try:
            rc_var.set(s.rate_control)
            if s.bitrate_kbps:
                br_var.set(str(s.bitrate_kbps))
            if s.vbr_quality is not None:
                vbr_var.set(
                    str(int(s.vbr_quality))
                    if float(s.vbr_quality) == int(s.vbr_quality)
                    else f"{s.vbr_quality:g}"
                )
                try:
                    vbr_scale.set(float(s.vbr_quality))
                except Exception:
                    pass
            if s.sample_rate:
                sr_var.set(str(s.sample_rate))
            else:
                sr_var.set("Source (keep)")
            if s.channels == 1:
                ch_var.set("Mono")
            elif s.channels == 2:
                ch_var.set("Stereo")
            else:
                ch_var.set("Source (keep)")
            if s.bit_depth:
                depth_var.set(str(s.bit_depth))
            if s.compression_level is not None:
                flac_var.set(str(s.compression_level))
                flac_scale.set(int(s.compression_level))
        finally:
            _suppress["n"] = False

    def _refresh_summary_from_ui() -> None:
        if use_advanced["v"]:
            s = _read_advanced()
            source_var.set("Using: custom advanced")
        else:
            s = _selected_preset_settings() or initial_settings
            source_var.set("Using: preset")
        summary_var.set(s.summary_line())

    def _on_preset_select(_event=None) -> None:
        if _suppress["n"]:
            return
        use_advanced["v"] = False
        s = _selected_preset_settings()
        if s is None:
            return
        preset_blurb.config(
            text=next(
                (
                    p.blurb or p.settings.summary_line()
                    for i, p in enumerate(shown_presets)
                    if preset_list.curselection()
                    and i == int(preset_list.curselection()[0])
                ),
                s.summary_line(),
            )
        )
        _apply_settings_to_advanced(s)
        _refresh_summary_from_ui()

    def _on_fmt_change(_event=None) -> None:
        use_advanced["v"] = False
        _reload_presets()
        s = _selected_preset_settings()
        if s is not None:
            _apply_settings_to_advanced(s)
        _refresh_summary_from_ui()

    def _on_advanced_edit(*_args) -> None:
        if _suppress["n"]:
            return
        use_advanced["v"] = True
        _refresh_summary_from_ui()

    # Wire events
    fmt_combo.bind("<<ComboboxSelected>>", _on_fmt_change)
    preset_list.bind("<<ListboxSelect>>", _on_preset_select)
    for var in (rc_var, br_var, vbr_var, sr_var, ch_var, depth_var, flac_var):
        var.trace_add("write", _on_advanced_edit)

    _reload_presets(select_id=initial_settings.preset_id)
    if start_custom:
        _apply_settings_to_advanced(initial_settings)
        try:
            nb.select(tab_advanced)
        except Exception:
            pass
    else:
        s0 = _selected_preset_settings()
        if s0 is not None:
            _apply_settings_to_advanced(s0)
    _refresh_summary_from_ui()

    result: list[ConfigDialogResult | None] = [None]

    def on_save() -> None:
        if use_advanced["v"]:
            s = _read_advanced()
        else:
            s = _selected_preset_settings()
            if s is None:
                s = resolve_settings(
                    send_format=_current_fmt_key(),
                    allowed_formats=allowed_send_formats,
                )
        s = resolve_settings(settings=s, allowed_formats=allowed_send_formats)
        raw = s.normalized_format()
        if raw not in VALID_SEND_FORMATS and raw not in allowed_tuple:
            messagebox.showerror("Config", f"Invalid format: {raw}", parent=dlg)
            return
        result[0] = ConfigDialogResult(
            send_format=raw,
            audio_encode=s,
            show_broken_video_presets=bool(broken_var.get()),
        )
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(foot)
    btn_row.pack(fill="x")
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Save", width=10, command=on_save).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        dlg.update_idletasks()
        # Fixed-ish wide default so preset names / advanced labels aren't clipped;
        # keep height moderate so it fits shorter displays.
        sh = int(dlg.winfo_screenheight() or 800)
        w = 800
        h = min(440, max(380, int(sh * 0.55)))
        h = min(h, int(sh * 0.72))
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        dlg.geometry(f"{w}x{h}+{px}+{py}")
        dlg.minsize(800, 360)
    except Exception:
        pass
    parent.wait_window(dlg)
    return result[0]


class ManageLibraryDialog:
    """Modeless Library roots manager (add / remove / rescan).

    Kept modeless so background scan/update can finish and refresh the list
    without blocking the main window.
    """

    def __init__(
        self,
        parent,
        *,
        get_roots: Callable[[], list[str]],
        on_add: Callable[[], None],
        on_remove: Callable[[list[str]], None],
        on_update: Callable[[], None],
        is_busy: Callable[[], bool],
        can_update: Callable[[], bool],
        on_exclusions: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._get_roots = get_roots
        self._on_add = on_add
        self._on_remove = on_remove
        self._on_update = on_update
        self._is_busy = is_busy
        self._can_update = can_update
        self._on_exclusions = on_exclusions
        self._on_close = on_close

        dlg = Toplevel(parent)
        dlg.title("Manage Library")
        dlg.transient(parent)
        dlg.minsize(480, 320)
        dlg.geometry("560x360")
        self._dlg = dlg

        body = Frame(dlg, padx=14, pady=12)
        body.pack(fill=BOTH, expand=True)

        Label(
            body,
            text="Library roots — folders scanned into the track list.",
            anchor="w",
            justify=LEFT,
        ).pack(fill="x")

        list_frame = Frame(body)
        list_frame.pack(fill=BOTH, expand=True, pady=(8, 8))
        scroll = Scrollbar(list_frame)
        scroll.pack(side=RIGHT, fill=Y)
        self._lb = Listbox(
            list_frame,
            yscrollcommand=scroll.set,
            selectmode="extended",
            activestyle="dotbox",
            exportselection=False,
        )
        self._lb.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.config(command=self._lb.yview)
        try:
            self._lb.configure(font=("Menlo", 11))
        except Exception:
            try:
                self._lb.configure(font=("Courier", 11))
            except Exception:
                pass

        self._status = Label(body, text="", anchor="w", justify=LEFT)
        self._status.pack(fill="x", pady=(0, 8))

        row = Frame(body)
        row.pack(fill="x")
        self._btn_add = Button(row, text="Add Root…", width=12, command=self._click_add)
        self._btn_add.pack(side=LEFT)
        self._btn_remove = Button(
            row, text="Remove Selected", width=14, command=self._click_remove
        )
        self._btn_remove.pack(side=LEFT, padx=(8, 0))
        self._btn_update = Button(
            row, text="Update Library", width=14, command=self._click_update
        )
        self._btn_update.pack(side=LEFT, padx=(8, 0))
        self._btn_exclusions = Button(
            row, text="Exclusions…", width=12, command=self._click_exclusions
        )
        self._btn_exclusions.pack(side=LEFT, padx=(8, 0))
        if on_exclusions is None:
            self._btn_exclusions.configure(state=DISABLED)
        Button(row, text="Close", width=10, command=self.close).pack(side=RIGHT)

        dlg.protocol("WM_DELETE_WINDOW", self.close)
        self._lb.bind("<Delete>", lambda _e: self._click_remove())
        self._lb.bind("<BackSpace>", lambda _e: self._click_remove())

        try:
            px = parent.winfo_rootx() + max(
                0, (parent.winfo_width() - 560) // 2
            )
            py = parent.winfo_rooty() + max(
                0, (parent.winfo_height() - 360) // 3
            )
            dlg.geometry(f"+{px}+{py}")
        except Exception:
            pass

        self.refresh()
        try:
            dlg.focus_set()
        except Exception:
            pass

    @property
    def window(self) -> Toplevel:
        """Underlying Tk window (for parenting file pickers / messageboxes)."""
        return self._dlg

    def is_open(self) -> bool:
        try:
            return bool(self._dlg.winfo_exists())
        except Exception:
            return False

    def focus(self) -> None:
        if not self.is_open():
            return
        try:
            self._dlg.lift()
            self._dlg.focus_force()
        except Exception:
            pass

    def close(self) -> None:
        dlg = self._dlg
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def refresh(self) -> None:
        """Reload root list and button enablement from callbacks."""
        if not self.is_open():
            return
        roots = list(self._get_roots() or [])
        selected = set(self._selected_paths())
        self._lb.delete(0, END)
        for path in roots:
            self._lb.insert(END, path)
            if path in selected:
                self._lb.selection_set(END)

        busy = False
        try:
            busy = bool(self._is_busy())
        except Exception:
            busy = False
        can_up = False
        try:
            can_up = bool(self._can_update())
        except Exception:
            can_up = False

        if busy:
            self._status.configure(text="Library is scanning or a job is running…")
        elif not roots:
            self._status.configure(text="No roots yet. Add a folder to build the library.")
        elif not can_up:
            self._status.configure(
                text="No reachable roots — reconnect volumes or add another folder."
            )
        else:
            n = len(roots)
            self._status.configure(
                text=f"{n} library root{'s' if n != 1 else ''}."
            )

        add_state = DISABLED if busy else NORMAL
        rem_state = DISABLED if busy or not roots else NORMAL
        upd_state = DISABLED if busy or not can_up else NORMAL
        try:
            self._btn_add.configure(state=add_state)
            self._btn_remove.configure(state=rem_state)
            self._btn_update.configure(state=upd_state)
        except Exception:
            pass

    def _selected_paths(self) -> list[str]:
        try:
            idxs = self._lb.curselection()
        except Exception:
            return []
        out: list[str] = []
        for i in idxs:
            try:
                out.append(str(self._lb.get(i)))
            except Exception:
                continue
        return out

    def _click_add(self) -> None:
        if self._is_busy():
            return
        self._on_add()

    def _click_remove(self) -> None:
        if self._is_busy():
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo(
                "Manage Library",
                "Select one or more roots to remove.",
                parent=self._dlg,
            )
            return
        if len(paths) == 1:
            msg = f"Remove this library root?\n\n{paths[0]}"
        else:
            msg = f"Remove {len(paths)} library roots?"
        if not messagebox.askyesno("Remove Library Root", msg, parent=self._dlg):
            return
        self._on_remove(paths)

    def _click_update(self) -> None:
        if self._is_busy() or not self._can_update():
            return
        self._on_update()

    def _click_exclusions(self) -> None:
        if self._on_exclusions is None:
            return
        self._on_exclusions()


def open_manage_library_dialog(
    parent,
    *,
    get_roots: Callable[[], list[str]],
    on_add: Callable[[], None],
    on_remove: Callable[[list[str]], None],
    on_update: Callable[[], None],
    is_busy: Callable[[], bool],
    can_update: Callable[[], bool],
    on_exclusions: Callable[[], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ManageLibraryDialog:
    """Open (or the caller reuses) the Manage Library window."""
    return ManageLibraryDialog(
        parent,
        get_roots=get_roots,
        on_add=on_add,
        on_remove=on_remove,
        on_update=on_update,
        is_busy=is_busy,
        can_update=can_update,
        on_exclusions=on_exclusions,
        on_close=on_close,
    )


class ExclusionsManagerDialog:
    """Modeless list of excluded file/folder paths with de-exclude actions."""

    def __init__(
        self,
        parent,
        *,
        get_exclusions: Callable[[], list[tuple[str, str]]],
        on_remove: Callable[[list[str]], None],
        is_busy: Callable[[], bool],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._get_exclusions = get_exclusions
        self._on_remove = on_remove
        self._is_busy = is_busy
        self._on_close = on_close
        # Display label → absolute path for selection mapping.
        self._path_by_display: dict[str, str] = {}

        dlg = Toplevel(parent)
        dlg.title("Exclusions Manager")
        dlg.transient(parent)
        dlg.minsize(520, 340)
        dlg.geometry("640x400")
        self._dlg = dlg

        body = Frame(dlg, padx=14, pady=12)
        body.pack(fill=BOTH, expand=True)

        Label(
            body,
            text=(
                "Excluded paths are skipped when scanning and removed from the "
                "library list. GUIDs stay in the index for device joins."
            ),
            anchor="w",
            justify=LEFT,
            wraplength=600,
        ).pack(fill="x")

        list_frame = Frame(body)
        list_frame.pack(fill=BOTH, expand=True, pady=(8, 8))
        scroll = Scrollbar(list_frame)
        scroll.pack(side=RIGHT, fill=Y)
        self._lb = Listbox(
            list_frame,
            yscrollcommand=scroll.set,
            selectmode="extended",
            activestyle="dotbox",
            exportselection=False,
        )
        self._lb.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.config(command=self._lb.yview)
        try:
            self._lb.configure(font=("Menlo", 11))
        except Exception:
            try:
                self._lb.configure(font=("Courier", 11))
            except Exception:
                pass

        self._status = Label(body, text="", anchor="w", justify=LEFT)
        self._status.pack(fill="x", pady=(0, 8))

        row = Frame(body)
        row.pack(fill="x")
        self._btn_remove = Button(
            row,
            text="Remove from Exclusions",
            width=20,
            command=self._click_remove,
        )
        self._btn_remove.pack(side=LEFT)
        Button(row, text="Close", width=10, command=self.close).pack(side=RIGHT)

        self._menu = Menu(dlg, tearoff=0)
        self._menu.add_command(
            label="Remove from exclusions",
            command=self._click_remove,
        )
        self._lb.bind("<Button-3>", self._popup_menu)
        self._lb.bind("<Button-2>", self._popup_menu)
        self._lb.bind("<Delete>", lambda _e: self._click_remove())
        self._lb.bind("<BackSpace>", lambda _e: self._click_remove())
        self._lb.bind("<Double-Button-1>", lambda _e: self._click_remove())

        dlg.protocol("WM_DELETE_WINDOW", self.close)
        try:
            px = parent.winfo_rootx() + max(
                0, (parent.winfo_width() - 640) // 2
            )
            py = parent.winfo_rooty() + max(
                0, (parent.winfo_height() - 400) // 3
            )
            dlg.geometry(f"+{px}+{py}")
        except Exception:
            pass

        self.refresh()
        try:
            dlg.focus_set()
        except Exception:
            pass

    @property
    def window(self) -> Toplevel:
        return self._dlg

    def is_open(self) -> bool:
        try:
            return bool(self._dlg.winfo_exists())
        except Exception:
            return False

    def focus(self) -> None:
        if not self.is_open():
            return
        try:
            self._dlg.lift()
            self._dlg.focus_force()
        except Exception:
            pass

    def close(self) -> None:
        dlg = self._dlg
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def refresh(self) -> None:
        if not self.is_open():
            return
        selected = set(self._selected_paths())
        rows = list(self._get_exclusions() or [])
        self._path_by_display.clear()
        self._lb.delete(0, END)
        for path, kind in rows:
            label = f"[{kind}] {path}"
            self._path_by_display[label] = path
            self._lb.insert(END, label)
            if path in selected:
                self._lb.selection_set(END)

        busy = False
        try:
            busy = bool(self._is_busy())
        except Exception:
            busy = False
        n = len(rows)
        if busy:
            self._status.configure(text="Library is busy…")
        elif n == 0:
            self._status.configure(text="No exclusions. Right-click media to exclude.")
        else:
            self._status.configure(
                text=f"{n} exclusion{'s' if n != 1 else ''}."
            )
        rem_state = DISABLED if busy or n == 0 else NORMAL
        try:
            self._btn_remove.configure(state=rem_state)
        except Exception:
            pass

    def _selected_paths(self) -> list[str]:
        try:
            idxs = self._lb.curselection()
        except Exception:
            return []
        out: list[str] = []
        for i in idxs:
            try:
                label = str(self._lb.get(i))
            except Exception:
                continue
            path = self._path_by_display.get(label)
            if path:
                out.append(path)
        return out

    def _popup_menu(self, event) -> str | None:
        try:
            idx = self._lb.nearest(event.y)
            if idx >= 0 and idx not in self._lb.curselection():
                self._lb.selection_clear(0, END)
                self._lb.selection_set(idx)
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._menu.grab_release()
            except Exception:
                pass
        return "break"

    def _click_remove(self) -> None:
        if self._is_busy():
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo(
                "Exclusions Manager",
                "Select one or more exclusions to remove.",
                parent=self._dlg,
            )
            return
        if len(paths) == 1:
            msg = (
                "Stop excluding this path?\n\n"
                f"{paths[0]}\n\n"
                "It will be scanned again if it is still under a library root."
            )
        else:
            msg = (
                f"Stop excluding {len(paths)} paths?\n\n"
                "They will be scanned again if still under a library root."
            )
        if not messagebox.askyesno(
            "Remove Exclusion", msg, parent=self._dlg
        ):
            return
        self._on_remove(paths)



@dataclass(frozen=True)
class SendVideoDialogResult:
    """User choices from Device → Send Video…"""

    parent_id: int
    encode_for_device: bool
    # Selected VideoEncodePreset.id when encoding with device options.
    preset_id: str | None = None
    # When True, encode ignores preset.max_fps (e.g. try 60 fps on ZEN).
    ignore_max_fps: bool = False


def _fill_preset_panel(parent: Frame, preset: VideoEncodePreset) -> None:
    """Render container / video / audio detail blocks for one preset tab."""
    for child in parent.winfo_children():
        child.destroy()

    sections = (
        ("Container", preset.container_detail or preset.container.upper()),
        ("Video codec", preset.video_detail or preset.video_codec),
        ("Audio codec", preset.audio_detail or preset.probe_audio_codec),
    )
    for title, text in sections:
        Label(parent, text=title, font=("", 11, "bold"), anchor="w").pack(
            fill="x", pady=(6, 0)
        )
        Label(
            parent,
            text=text,
            justify=LEFT,
            wraplength=420,
            anchor="w",
        ).pack(fill="x", padx=(8, 0))

    extra: list[str] = []
    if preset.max_fps and preset.max_fps > 0:
        extra.append(
            f"Frame rate: keep source if ≤ {preset.max_fps:g} fps, else cap"
        )
    else:
        extra.append("Frame rate: keep source")
    if preset.qscale_v is not None:
        extra.append(f"Video quality: qscale {preset.qscale_v}")
    elif preset.video_bitrate:
        extra.append(f"Video bitrate: {preset.video_bitrate}")
    extra.append(
        f"Audio: {preset.audio_bitrate} · {preset.audio_sample_rate} Hz · "
        f"{preset.audio_channels} channel(s)"
    )
    if preset.broken:
        extra.append("⚠ Broken — does not play reliably on this device")
    elif preset.experimental:
        extra.append("⚠ Experimental — may not play on this device")

    Label(parent, text="Parameters", font=("", 11, "bold"), anchor="w").pack(
        fill="x", pady=(10, 0)
    )
    Label(
        parent,
        text="\n".join(extra),
        justify=LEFT,
        wraplength=420,
        anchor="w",
    ).pack(fill="x", padx=(8, 0), pady=(0, 6))


def ask_video_destination(
    parent,
    *,
    filename: str = "",
    video_options: DeviceVideoOptions | None = None,
    encode_default: bool = True,
    include_broken_presets: bool = False,
    video_folder_id: int | None = None,
    tv_folder_id: int | None = None,
    video_folder_name: str = "Video",
    tv_folder_name: str = "TV",
) -> SendVideoDialogResult | None:
    """Ask Video/TV parent and optional device encode preset. None if cancelled.

    *video_options* is only set for known players (e.g. ZEN Vision:M). The
    generic device profile passes None — no format notebook is shown.
    *include_broken_presets* comes from Config (show broken recipes like WMV).

    *video_folder_id* / *tv_folder_id* come from a live folder-name resolution
    when available; defaults fall back to legacy Vision:M ids (120 / 124).
    """
    vid = (
        int(video_folder_id)
        if video_folder_id is not None
        else DEFAULT_VIDEO_FOLDER_ID
    )
    tid = (
        int(tv_folder_id) if tv_folder_id is not None else DEFAULT_TV_FOLDER_ID
    )
    vname = (video_folder_name or "").strip() or ZEN_VISION_M_FOLDER_IDS.get(
        vid, "Video"
    )
    tname = (tv_folder_name or "").strip() or ZEN_VISION_M_FOLDER_IDS.get(
        tid, "TV"
    )

    dlg = Toplevel(parent)
    dlg.title("Send Video")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    label = filename.strip() or "selected file"
    Label(
        body,
        text=f"Send to device:\n\n{label}",
        justify=LEFT,
        wraplength=440,
    ).pack(anchor="w", pady=(0, 10))

    Label(body, text="Destination folder:", anchor="w").pack(fill="x")
    choice = StringVar(value="video")
    Radiobutton(
        body,
        text=_video_folder_radio_label("Video", vid, vname),
        variable=choice,
        value="video",
        anchor="w",
    ).pack(fill="x", pady=2)
    Radiobutton(
        body,
        text=_video_folder_radio_label("TV show", tid, tname),
        variable=choice,
        value="tv",
        anchor="w",
    ).pack(fill="x", pady=2)

    visible: tuple[VideoEncodePreset, ...] = ()
    if video_options is not None:
        visible = video_options.visible_presets(
            include_broken=bool(include_broken_presets)
        )
    has_options = bool(visible)
    encode_var = BooleanVar(value=bool(encode_default and has_options))
    ignore_max_fps_var = BooleanVar(value=False)
    high_fps_cb: Checkbutton | None = None
    notebook: ttk.Notebook | None = None
    preset_by_tab: dict[int, VideoEncodePreset] = {}
    default_preset: VideoEncodePreset | None = None
    if has_options and video_options is not None:
        default_preset = video_options.default_preset()
        if default_preset not in visible:
            default_preset = visible[0]

    def _selected_preset() -> VideoEncodePreset | None:
        if notebook is None or not has_options:
            return default_preset
        try:
            idx = int(notebook.index("current"))
        except Exception:
            return default_preset
        return preset_by_tab.get(idx, default_preset)

    def _sync_high_fps_for_preset(preset: VideoEncodePreset | None) -> None:
        if high_fps_cb is None:
            return
        cap = float(preset.max_fps or 0) if preset is not None else 0.0
        encode_on = bool(encode_var.get())
        if encode_on and cap > 0:
            try:
                high_fps_cb.configure(
                    state="normal",
                    text=(
                        f"Ignore max frame rate ({cap:g} fps) (experimental)"
                    ),
                )
            except Exception:
                pass
        else:
            ignore_max_fps_var.set(False)
            try:
                high_fps_cb.configure(state="disabled")
            except Exception:
                pass

    def _on_tab_changed(_event=None) -> None:
        _sync_high_fps_for_preset(_selected_preset())

    if has_options and video_options is not None:
        Label(
            body,
            text=f"Device options — {video_options.device_display_name}",
            font=("", 11, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(14, 2))

        Checkbutton(
            body,
            text="Encode for this device",
            variable=encode_var,
            anchor="w",
            justify=LEFT,
        ).pack(fill="x", pady=(2, 4))

        Label(
            body,
            text=(
                "Each tab is a mutually exclusive format recipe "
                "(container + video + audio). Default is "
                "AVI · XviD / MPEG-4 SP · MP3."
            ),
            justify=LEFT,
            wraplength=440,
        ).pack(anchor="w", pady=(0, 6))

        notebook = ttk.Notebook(body)
        notebook.pack(fill=BOTH, expand=True, pady=(0, 4))

        for i, preset in enumerate(visible):
            tab = Frame(notebook, padx=10, pady=6)
            notebook.add(tab, text=preset.tab_label)
            _fill_preset_panel(tab, preset)
            preset_by_tab[i] = preset
            if default_preset is not None and preset.id == default_preset.id:
                notebook.select(i)

        notebook.bind("<<NotebookTabChanged>>", _on_tab_changed)

        high_fps_cb = Checkbutton(
            body,
            text="Ignore max frame rate (experimental)",
            variable=ignore_max_fps_var,
            anchor="w",
            justify=LEFT,
        )
        high_fps_cb.pack(fill="x", pady=(6, 2))
        Label(
            body,
            text=(
                "Keeps the source frame rate even when above the selected "
                "recipe’s cap. Likely to fail or glitch — experiments only."
            ),
            justify=LEFT,
            wraplength=440,
        ).pack(anchor="w", pady=(0, 4))

        def _sync_encode_state(*_args) -> None:
            on = bool(encode_var.get())
            try:
                if notebook is not None:
                    for i in range(len(visible)):
                        notebook.tab(i, state="normal" if on else "disabled")
            except Exception:
                pass
            _sync_high_fps_for_preset(_selected_preset() if on else None)

        encode_var.trace_add("write", _sync_encode_state)
        _sync_encode_state()
        _on_tab_changed()
    else:
        Label(
            body,
            text=(
                "No device-specific video options for this player — "
                "file will be sent as-is.\n"
                "ObjectFileName is the sanitized host basename."
            ),
            justify=LEFT,
            wraplength=440,
        ).pack(anchor="w", pady=(12, 8))

    result: list[SendVideoDialogResult | None] = [None]

    def on_send() -> None:
        parent_id = tid if choice.get() == "tv" else vid
        do_encode = bool(encode_var.get()) and has_options
        preset = _selected_preset() if do_encode else None
        cap = float(preset.max_fps or 0) if preset is not None else 0.0
        result[0] = SendVideoDialogResult(
            parent_id=int(parent_id),
            encode_for_device=do_encode,
            preset_id=preset.id if preset is not None else None,
            ignore_max_fps=(
                do_encode and cap > 0 and bool(ignore_max_fps_var.get())
            ),
        )
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(10, 0))
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Send", width=10, command=on_send).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 480) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 420) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)
    return result[0]


def show_folder_list_dialog(parent, folders: list) -> None:
    """Modal scrollable list of device folders (does not touch the library tree)."""
    from tkinter import BOTH, END, LEFT, RIGHT, Y, Listbox, Scrollbar

    dlg = Toplevel(parent)
    dlg.title("Device Folders")
    dlg.transient(parent)
    dlg.geometry("420x360")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)
    Label(body, text=f"{len(folders)} folder(s)").pack(anchor="w")

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    scroll = Scrollbar(list_frame)
    scroll.pack(side=RIGHT, fill=Y)
    lb = Listbox(list_frame, yscrollcommand=scroll.set)
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    scroll.config(command=lb.yview)
    for entry in folders:
        lb.insert(END, folder_line(entry))

    Button(body, text="Close", command=dlg.destroy).pack(anchor="e")
    dlg.grab_set()
    parent.wait_window(dlg)


def show_file_list_dialog(parent, files: list) -> None:
    """Modal scrollable list of device files (experimental List Files)."""
    from tkinter import BOTH, END, LEFT, RIGHT, Y, Listbox, Scrollbar

    from mtpmanager.ui.formatting import file_line

    dlg = Toplevel(parent)
    dlg.title("Device Files (experimental)")
    dlg.transient(parent)
    dlg.geometry("720x420")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=(
            f"{len(files)} object(s) — full MTP file listing. "
            "May be large/slow on big libraries."
        ),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w")

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    yscroll = Scrollbar(list_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    xscroll = Scrollbar(list_frame, orient="horizontal")
    xscroll.pack(side="bottom", fill="x")
    lb = Listbox(
        list_frame,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
    )
    # Prefer monospaced font for aligned columns when available.
    try:
        lb.configure(font=("Menlo", 11))
    except Exception:
        try:
            lb.configure(font=("Courier", 11))
        except Exception:
            pass
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=lb.yview)
    xscroll.config(command=lb.xview)
    for entry in files:
        lb.insert(END, file_line(entry))

    Button(body, text="Close", command=dlg.destroy).pack(anchor="e")
    dlg.grab_set()
    parent.wait_window(dlg)


def show_track_list_dialog(
    parent,
    tracks: list,
    *,
    on_load_tags: Callable | None = None,
) -> None:
    """Modal scrollable list of device tracks (experimental List Tracks).

    Rows come from the fast file listing (ids/filenames). Optional
    *on_load_tags(selected_refs, apply_updates)* starts a background tag
    fetch; *apply_updates(updated_refs)* must be called on the UI thread
    with the enriched refs for those ids.
    """
    from tkinter import BOTH, END, EXTENDED, LEFT, RIGHT, Y, Listbox, Scrollbar

    from mtpmanager.ui.formatting import track_line

    rows = list(tracks or [])
    loading = {"active": False}

    dlg = Toplevel(parent)
    dlg.title("Device Tracks (experimental)")
    dlg.transient(parent)
    dlg.geometry("860x460")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)

    note_var = StringVar()
    status_var = StringVar(value="")

    def _tagged_count() -> int:
        return sum(
            1
            for t in rows
            if (getattr(t, "title", None) or getattr(t, "artist", None) or "").strip()
        )

    def _refresh_note() -> None:
        tagged = _tagged_count()
        note_var.set(
            f"{len(rows)} track(s) from file listing "
            f"({tagged} with artist/title tags). "
            "Filenames first — select rows and Load tags for on-device "
            "metadata (per-object USB; keep selections small)."
        )

    _refresh_note()
    Label(
        body,
        textvariable=note_var,
        wraplength=820,
        justify=LEFT,
    ).pack(anchor="w")
    Label(
        body,
        textvariable=status_var,
        wraplength=820,
        justify=LEFT,
        fg="#444",
    ).pack(anchor="w", pady=(2, 0))

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    yscroll = Scrollbar(list_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    xscroll = Scrollbar(list_frame, orient="horizontal")
    xscroll.pack(side="bottom", fill="x")
    lb = Listbox(
        list_frame,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
        selectmode=EXTENDED,
        exportselection=False,
    )
    try:
        lb.configure(font=("Menlo", 11))
    except Exception:
        try:
            lb.configure(font=("Courier", 11))
        except Exception:
            pass
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=lb.yview)
    xscroll.config(command=lb.xview)

    def _rebuild_list(*, keep_ids: set[int] | None = None) -> None:
        selected_ids = keep_ids
        if selected_ids is None:
            selected_ids = set()
            for idx in lb.curselection():
                i = int(idx)
                if 0 <= i < len(rows):
                    selected_ids.add(int(rows[i].item_id or 0))
        lb.delete(0, END)
        for entry in rows:
            lb.insert(END, track_line(entry))
        if selected_ids:
            for i, entry in enumerate(rows):
                if int(entry.item_id or 0) in selected_ids:
                    lb.selection_set(i)

    _rebuild_list(keep_ids=set())

    btn_row = Frame(body)
    btn_row.pack(fill="x")

    def on_close() -> None:
        if loading["active"]:
            if not messagebox.askyesno(
                "Close track list",
                "Tag loading is still running.\n\nClose the dialog anyway?",
                parent=dlg,
            ):
                return
        dlg.destroy()

    def apply_updates(updated_refs: list) -> None:
        """Merge enriched refs into the open dialog (UI thread)."""
        if not dlg.winfo_exists():
            return
        by_id = {int(r.item_id or 0): r for r in (updated_refs or []) if int(r.item_id or 0) > 0}
        if by_id:
            for i, ref in enumerate(rows):
                oid = int(ref.item_id or 0)
                if oid in by_id:
                    rows[i] = by_id[oid]
            _rebuild_list()
            _refresh_note()
        loading["active"] = False
        try:
            btn_tags.configure(state="normal")
        except Exception:
            pass

    def on_load_clicked() -> None:
        if on_load_tags is None:
            return
        if loading["active"]:
            return
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo(
                "Load tags",
                "Select one or more tracks first.\n\n"
                "Tip: keep selections small — each tag fetch is a USB round-trip.",
                parent=dlg,
            )
            return
        selected = []
        for idx in sel:
            i = int(idx)
            if 0 <= i < len(rows):
                selected.append(rows[i])
        if not selected:
            return
        # Soft cap: warn on large selections (still allowed).
        if len(selected) > 25 and not messagebox.askyesno(
            "Load tags",
            f"Load on-device tags for {len(selected)} tracks?\n\n"
            "Each object is a separate USB metadata call. Large batches "
            "can take a long time and stress the device session.\n\n"
            "Continue?",
            parent=dlg,
            icon=messagebox.WARNING,
        ):
            return
        loading["active"] = True
        btn_tags.configure(state="disabled")
        status_var.set(f"Loading tags for {len(selected)} track(s)…")

        def apply_and_status(updated_refs: list, *, message: str = "") -> None:
            apply_updates(updated_refs)
            if dlg.winfo_exists():
                status_var.set(message or "")

        on_load_tags(selected, apply_and_status)

    Button(btn_row, text="Close", width=10, command=on_close).pack(side=RIGHT)
    btn_tags = Button(
        btn_row,
        text="Load tags for selection",
        command=on_load_clicked,
        state="normal" if on_load_tags is not None else "disabled",
    )
    btn_tags.pack(side=RIGHT, padx=(0, 8))

    dlg.protocol("WM_DELETE_WINDOW", on_close)
    dlg.grab_set()
    parent.wait_window(dlg)


def pick_file_entry_dialog(
    parent,
    files: list,
    *,
    title: str = "Select Object",
    prompt: str = "Select an object from the list.",
    action_label: str = "Select",
    confirm_message=None,
):
    """Modal picker over a file listing; returns selected FileEntry or None.

    Used by experimental Device admin paths that start from get_filelisting /
    list_files (Delete Track, Get File Info). Optional *confirm_message(entry)*
    returns a yes/no body string, or None to skip confirmation.
    """
    from tkinter import BOTH, END, LEFT, RIGHT, Y, Listbox, Scrollbar

    from mtpmanager.domain.models import FileEntry
    from mtpmanager.ui.formatting import file_line

    entries = list(files or [])
    result: list[FileEntry | None] = [None]

    dlg = Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.geometry("720x420")

    body = Frame(dlg, padx=10, pady=10)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=f"{len(entries)} object(s) — {prompt}",
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w")

    list_frame = Frame(body)
    list_frame.pack(fill=BOTH, expand=True, pady=(6, 8))
    yscroll = Scrollbar(list_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    xscroll = Scrollbar(list_frame, orient="horizontal")
    xscroll.pack(side="bottom", fill="x")
    lb = Listbox(
        list_frame,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
        exportselection=False,
    )
    try:
        lb.configure(font=("Menlo", 11))
    except Exception:
        try:
            lb.configure(font=("Courier", 11))
        except Exception:
            pass
    lb.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=lb.yview)
    xscroll.config(command=lb.xview)
    for entry in entries:
        lb.insert(END, file_line(entry))

    btn_row = Frame(body)
    btn_row.pack(fill="x")

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    def on_choose() -> None:
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo(
                title,
                "Select an object from the list first.",
                parent=dlg,
            )
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(entries):
            return
        entry = entries[idx]
        if confirm_message is not None:
            body_text = confirm_message(entry)
            if body_text and not messagebox.askyesno(
                "Confirm",
                body_text,
                parent=dlg,
            ):
                return
        result[0] = entry
        dlg.destroy()

    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text=action_label, width=10, command=on_choose).pack(side=RIGHT)

    def on_double(_event=None) -> None:
        on_choose()

    lb.bind("<Double-Button-1>", on_double)
    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 720) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 420) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    if entries:
        lb.selection_set(0)
        lb.activate(0)
        lb.focus_set()
    parent.wait_window(dlg)
    return result[0]


def show_file_info_dialog(parent, entry, *, note: str | None = None) -> None:
    """Modal display of one object's metadata (Get File Info).

    Optional *note* is shown under the summary (e.g. listing-snapshot fallback
    when live Get_Filemetadata fails on ZEN).
    """
    from mtpmanager.ui.formatting import file_metadata_summary

    dlg = Toplevel(parent)
    dlg.title("File Info (experimental)")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)
    Label(
        body,
        text=file_metadata_summary(entry),
        justify=LEFT,
        anchor="w",
        font=("Menlo", 11),
    ).pack(anchor="w")
    if note:
        Label(
            body,
            text=note,
            justify=LEFT,
            anchor="w",
            wraplength=420,
            fg="#555555",
        ).pack(anchor="w", pady=(10, 0))
    Button(body, text="Close", width=10, command=dlg.destroy).pack(
        anchor="e", pady=(12, 0)
    )
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 420) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 240) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)


def show_track_info_dialog(
    parent,
    info,
    *,
    title: str = "Track Info",
    extra_lines: list[str] | None = None,
    text: str | None = None,
) -> None:
    """Modal display of on-device track tags / codec / size details.

    *info* is a :class:`~mtpmanager.domain.models.DeviceTrackInfo` (or compatible).
    Pass *text* to show a fully preformatted body instead of building from *info*.
    """
    from tkinter import Text

    from mtpmanager.ui.formatting import track_metadata_summary

    dlg = Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.resizable(True, True)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    body_text = text if text is not None else track_metadata_summary(
        info, extra_lines=extra_lines
    )

    text_frame = Frame(body)
    text_frame.pack(fill=BOTH, expand=True)
    yscroll = Scrollbar(text_frame)
    yscroll.pack(side=RIGHT, fill=Y)
    txt = Text(
        text_frame,
        width=56,
        height=24,
        wrap="word",
        font=("Menlo", 11),
        yscrollcommand=yscroll.set,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
    )
    txt.pack(side=LEFT, fill=BOTH, expand=True)
    yscroll.config(command=txt.yview)
    txt.insert("1.0", body_text)
    txt.configure(state=DISABLED)

    Button(body, text="Close", width=10, command=dlg.destroy).pack(
        anchor="e", pady=(12, 0)
    )
    dlg.grab_set()
    try:
        w, h = 520, 520
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        dlg.geometry(f"{w}x{h}+{px}+{py}")
        dlg.minsize(400, 320)
    except Exception:
        pass
    parent.wait_window(dlg)


@dataclass
class AddToPlaylistResult:
    """Outcome of the Add to Playlist dialog."""

    playlist_id: int
    playlist_name: str
    skip_existing: bool = True
    # True when the user created/deleted playlists (caller may refresh tab).
    playlists_changed: bool = False


def ask_add_to_playlist(
    parent,
    *,
    candidate_tracks: list,
    list_playlists: Callable[[], list],
    create_playlist: Callable[[str], object],
    delete_playlist: Callable[[int], bool],
) -> AddToPlaylistResult | None:
    """Modal: pick (or create) a playlist and add *candidate_tracks*.

    No post-add confirmation — the caller reports status non-modally.
    *list_playlists* returns a sequence of objects with ``.id``, ``.name``,
    and optional ``.track_count``. *create_playlist(name)* returns an object
    with ``.id`` / ``.name``. *delete_playlist(id)* returns success bool.

    Returns :class:`AddToPlaylistResult` or None if cancelled.
    """
    from mtpmanager.domain.library import primary_artist

    tracks = list(candidate_tracks or [])
    result: list[AddToPlaylistResult | None] = [None]
    changed = [False]

    dlg = Toplevel(parent)
    dlg.title("Add to Playlist")
    dlg.transient(parent)
    dlg.resizable(True, True)

    body = Frame(dlg, padx=12, pady=10)
    body.pack(fill=BOTH, expand=True)

    # --- Candidates (add mode only) ---
    if tracks:
        Label(
            body,
            text=f"Tracks to add ({len(tracks)})",
            font=("", 11, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))
        cand_frame = Frame(body)
        cand_frame.pack(fill=BOTH, expand=True, pady=(0, 8))
        cand_scroll = Scrollbar(cand_frame)
        cand_scroll.pack(side=RIGHT, fill=Y)
        cand_list = Listbox(
            cand_frame,
            height=min(8, max(3, len(tracks))),
            yscrollcommand=cand_scroll.set,
            exportselection=False,
        )
        cand_list.pack(side=LEFT, fill=BOTH, expand=True)
        cand_scroll.config(command=cand_list.yview)
        for t in tracks:
            title = (t.meta.title if t.meta else "") or "Unknown Title"
            artist = primary_artist(t) if t else ""
            cand_list.insert(END, f"{artist} — {title}" if artist else title)

    Label(body, text="Playlists", font=("", 11, "bold"), anchor="w").pack(
        anchor="w", pady=(0, 4)
    )
    pl_frame = Frame(body)
    pl_frame.pack(fill=BOTH, expand=True)
    pl_scroll = Scrollbar(pl_frame)
    pl_scroll.pack(side=RIGHT, fill=Y)
    pl_list = Listbox(
        pl_frame,
        height=8,
        yscrollcommand=pl_scroll.set,
        exportselection=False,
    )
    pl_list.pack(side=LEFT, fill=BOTH, expand=True)
    pl_scroll.config(command=pl_list.yview)

    # id list parallel to listbox rows
    pl_ids: list[int] = []

    def refresh_playlists(*, select_id: int | None = None) -> None:
        pl_list.delete(0, END)
        pl_ids.clear()
        items = list(list_playlists() or [])
        for info in items:
            pid = int(getattr(info, "id", 0) or 0)
            name = str(getattr(info, "name", "") or "")
            count = getattr(info, "track_count", None)
            label = f"{name}  ({count})" if count is not None else name
            pl_list.insert(END, label)
            pl_ids.append(pid)
        if not pl_ids:
            btn_add.configure(state=DISABLED)
            btn_del.configure(state=DISABLED)
            return
        btn_del.configure(state=NORMAL)
        btn_add.configure(state=NORMAL if tracks else DISABLED)
        idx = 0
        if select_id is not None and select_id in pl_ids:
            idx = pl_ids.index(select_id)
        pl_list.selection_clear(0, END)
        pl_list.selection_set(idx)
        pl_list.see(idx)

    skip_var = BooleanVar(value=True)
    Checkbutton(
        body,
        text="Skip tracks already in the playlist",
        variable=skip_var,
    ).pack(anchor="w", pady=(6, 2))

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(8, 0))

    def on_new() -> None:
        name = ask_text(
            dlg,
            title="New Playlist",
            prompt="Playlist name:",
        )
        if not name:
            return
        try:
            created = create_playlist(name)
        except ValueError as e:
            messagebox.showerror("Playlist", str(e), parent=dlg)
            return
        except Exception as e:
            messagebox.showerror("Playlist", f"Could not create playlist:\n{e}", parent=dlg)
            return
        changed[0] = True
        refresh_playlists(select_id=int(getattr(created, "id", 0) or 0))

    def on_delete() -> None:
        sel = pl_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(pl_ids):
            return
        pid = pl_ids[idx]
        label = pl_list.get(idx)
        if not messagebox.askyesno(
            "Delete Playlist",
            f"Delete playlist?\n\n{label}",
            parent=dlg,
        ):
            return
        try:
            ok = bool(delete_playlist(pid))
        except Exception as e:
            messagebox.showerror("Playlist", f"Delete failed:\n{e}", parent=dlg)
            return
        if not ok:
            messagebox.showwarning("Playlist", "Playlist was not found.", parent=dlg)
        changed[0] = True
        refresh_playlists()

    def on_add() -> None:
        sel = pl_list.curselection()
        if not sel or not tracks:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(pl_ids):
            return
        pid = pl_ids[idx]
        name = pl_list.get(idx).rsplit("  (", 1)[0]
        result[0] = AddToPlaylistResult(
            playlist_id=pid,
            playlist_name=name,
            skip_existing=bool(skip_var.get()),
            playlists_changed=changed[0],
        )
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_new = Button(btn_row, text="+", width=3, command=on_new)
    btn_new.pack(side=LEFT, padx=(0, 4))
    btn_del = Button(btn_row, text="−", width=3, command=on_delete, state=DISABLED)
    btn_del.pack(side=LEFT, padx=(0, 8))
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(side=RIGHT)
    btn_add = Button(
        btn_row,
        text="Add to selected",
        width=14,
        command=on_add,
        state=DISABLED,
    )
    btn_add.pack(side=RIGHT, padx=(0, 6))

    refresh_playlists()
    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 480) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 520) // 3)
        dlg.geometry(f"480x520+{px}+{py}")
    except Exception:
        dlg.geometry("480x520")
    parent.wait_window(dlg)

    out = result[0]
    if out is None and changed[0]:
        # User edited playlists but cancelled add — still signal refresh.
        return AddToPlaylistResult(
            playlist_id=-1,
            playlist_name="",
            skip_existing=True,
            playlists_changed=True,
        )
    return out


# ---------------------------------------------------------------------------
# Podcast Settings (Library → Podcast Settings…)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PodcastSettingsResult:
    """Values saved from Library → Podcast Settings…"""

    auto_enabled: bool
    schedule_days: tuple[str, ...]
    schedule_time: str
    max_new_per_show: int
    auto_sync_to_device: bool
    run_full_sync_now: bool = False
    # When True, *audiobook_audio_encode* overrides Config for Audiobooks tab.
    use_audiobook_encode_override: bool = False
    audiobook_audio_encode: AudioEncodeSettings | None = None
    # When True, *podcast_audio_encode* overrides Config for podcast episodes.
    use_podcast_encode_override: bool = False
    podcast_audio_encode: AudioEncodeSettings | None = None


@dataclass(frozen=True)
class PodcastShowEncodeResult:
    """Values saved from a single-show encode dialog (context menu)."""

    # True when the show uses a custom recipe; False clears the override.
    use_override: bool
    audio_encode: AudioEncodeSettings | None = None


def _time_spinner_row(
    parent,
    *,
    initial_hhmm: str,
) -> tuple[Frame, Callable[[], str]]:
    """Hour / minute / AM·PM with ± buttons, keyboard entry, Tab advance.

    Returns (frame, getter) where getter() → normalized HH:MM 24h.
    """
    hour0, minute0, ampm0 = hhmm_to_12h(initial_hhmm)
    row = Frame(parent)
    hour_var = StringVar(value=str(hour0))
    min_var = StringVar(value=f"{minute0:02d}")
    ampm_var = StringVar(value=ampm0)

    def _clamp_hour() -> int:
        try:
            h = int(str(hour_var.get()).strip() or "12")
        except ValueError:
            h = 12
        h = max(1, min(12, h))
        hour_var.set(str(h))
        return h

    def _clamp_min() -> int:
        raw = str(min_var.get()).strip()
        try:
            m = int(raw or "0")
        except ValueError:
            m = 0
        m = max(0, min(59, m))
        min_var.set(f"{m:02d}")
        return m

    def bump_hour(delta: int) -> None:
        h = _clamp_hour()
        h = ((h - 1 + delta) % 12) + 1
        hour_var.set(str(h))

    def bump_min(delta: int) -> None:
        m = _clamp_min()
        m = (m + delta) % 60
        min_var.set(f"{m:02d}")

    def toggle_ampm() -> None:
        cur = str(ampm_var.get()).strip().upper()
        ampm_var.set("PM" if cur.startswith("A") else "AM")

    def get_hhmm() -> str:
        return components_to_hhmm(_clamp_hour(), _clamp_min(), ampm_var.get())

    # Hour
    hour_col = Frame(row)
    hour_col.pack(side=LEFT)
    Label(hour_col, text="Hour").pack()
    Button(hour_col, text="▲", width=3, command=lambda: bump_hour(1)).pack()
    hour_entry = Entry(hour_col, textvariable=hour_var, width=3, justify="center")
    hour_entry.pack(pady=2)
    Button(hour_col, text="▼", width=3, command=lambda: bump_hour(-1)).pack()

    Label(row, text=":", font=("", 14, "bold")).pack(side=LEFT, padx=4, pady=(14, 0))

    # Minute
    min_col = Frame(row)
    min_col.pack(side=LEFT)
    Label(min_col, text="Min").pack()
    Button(min_col, text="▲", width=3, command=lambda: bump_min(1)).pack()
    min_entry = Entry(min_col, textvariable=min_var, width=3, justify="center")
    min_entry.pack(pady=2)
    Button(min_col, text="▼", width=3, command=lambda: bump_min(-1)).pack()

    # AM/PM
    ampm_col = Frame(row)
    ampm_col.pack(side=LEFT, padx=(10, 0))
    Label(ampm_col, text="AM/PM").pack()
    Button(ampm_col, text="▲", width=4, command=toggle_ampm).pack()
    ampm_entry = Entry(
        ampm_col, textvariable=ampm_var, width=4, justify="center"
    )
    ampm_entry.pack(pady=2)
    Button(ampm_col, text="▼", width=4, command=toggle_ampm).pack()

    def on_hour_return(_e=None) -> str:
        _clamp_hour()
        min_entry.focus_set()
        min_entry.selection_range(0, END)
        return "break"

    def on_min_return(_e=None) -> str:
        _clamp_min()
        ampm_entry.focus_set()
        ampm_entry.selection_range(0, END)
        return "break"

    def on_ampm_return(_e=None) -> str:
        raw = str(ampm_var.get()).strip().upper()
        if raw.startswith("P"):
            ampm_var.set("PM")
        else:
            ampm_var.set("AM")
        return "break"

    def on_hour_tab(_e=None) -> str:
        return on_hour_return()

    def on_min_tab(_e=None) -> str:
        return on_min_return()

    hour_entry.bind("<Return>", on_hour_return)
    hour_entry.bind("<Tab>", on_hour_tab)
    hour_entry.bind("<FocusOut>", lambda _e: _clamp_hour())
    min_entry.bind("<Return>", on_min_return)
    min_entry.bind("<Tab>", on_min_tab)
    min_entry.bind("<FocusOut>", lambda _e: _clamp_min())
    ampm_entry.bind("<Return>", on_ampm_return)
    ampm_entry.bind("<FocusOut>", on_ampm_return)

    return row, get_hhmm


def _pack_encode_override_section(
    parent,
    *,
    title: str,
    blurb: str,
    use_override: bool,
    initial: AudioEncodeSettings,
    allowed_send_formats: frozenset[str] | None,
    checkbox_text: str = "Use custom encode",
    list_height: int = 5,
) -> tuple[BooleanVar, Callable[[], AudioEncodeSettings]]:
    """Compact format+preset picker with enable checkbox.

    Returns ``(override_var, get_settings)``.
    """
    allowed_tuple = formats_allowed(allowed_send_formats)
    if (title or "").strip():
        Label(
            parent,
            text=title,
            font=("", 11, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
    if (blurb or "").strip():
        Label(
            parent,
            text=blurb,
            justify=LEFT,
            wraplength=440,
            fg="#333",
        ).pack(anchor="w", pady=(0, 6))

    override_var = BooleanVar(value=bool(use_override))
    Checkbutton(
        parent,
        text=checkbox_text,
        variable=override_var,
        anchor="w",
    ).pack(fill="x", pady=(0, 4))

    controls = Frame(parent)
    controls.pack(fill="x", pady=(0, 4))

    fmt_row = Frame(controls)
    fmt_row.pack(fill="x", pady=(0, 4))
    Label(fmt_row, text="Output format:").pack(side=LEFT)
    fmt_keys = list(allowed_tuple)
    fmt_labels = [format_display_name(f) for f in fmt_keys]
    label_to_key = dict(zip(fmt_labels, fmt_keys))
    key_to_label = dict(zip(fmt_keys, fmt_labels))
    ui_fmt = initial.normalized_format()
    if ui_fmt == "aac" and "m4a" in fmt_keys and "aac" not in fmt_keys:
        ui_fmt = "m4a"
    if ui_fmt not in fmt_keys:
        ui_fmt = fmt_keys[0] if fmt_keys else "mp3"
    fmt_var = StringVar(
        value=key_to_label.get(ui_fmt, fmt_labels[0] if fmt_labels else "MP3")
    )
    fmt_combo = ttk.Combobox(
        fmt_row,
        textvariable=fmt_var,
        values=fmt_labels,
        state="readonly",
        width=16,
    )
    fmt_combo.pack(side=LEFT, padx=(8, 0))

    Label(controls, text="Quality:", anchor="w").pack(fill="x")
    preset_frame = Frame(controls)
    preset_frame.pack(fill="x", pady=(2, 0))
    preset_scroll = Scrollbar(preset_frame)
    preset_scroll.pack(side=RIGHT, fill=Y)
    preset_list = Listbox(
        preset_frame,
        height=list_height,
        exportselection=False,
        yscrollcommand=preset_scroll.set,
        width=52,
    )
    preset_list.pack(side=LEFT, fill="x", expand=True)
    preset_scroll.config(command=preset_list.yview)

    preset_ids: list[str] = []

    def current_fmt_key() -> str:
        lab = str(fmt_var.get() or "")
        return label_to_key.get(lab, fmt_keys[0] if fmt_keys else "mp3")

    def refill_presets(*, select_id: str | None = None) -> None:
        nonlocal preset_ids
        preset_list.delete(0, END)
        preset_ids = []
        fmt = current_fmt_key()
        ladder = presets_for_format(fmt)
        want = select_id
        if want and not any(p.id == want for p in ladder):
            want = None
        chosen_idx = 0
        for i, p in enumerate(ladder):
            preset_list.insert(END, p.display_name)
            preset_ids.append(p.id)
            if want and p.id == want:
                chosen_idx = i
            elif not want and p.id == initial.preset_id:
                chosen_idx = i
        if preset_ids:
            preset_list.selection_clear(0, END)
            preset_list.selection_set(chosen_idx)
            preset_list.see(chosen_idx)

    def get_settings() -> AudioEncodeSettings:
        sel = preset_list.curselection()
        if sel and preset_ids:
            pid = preset_ids[int(sel[0])]
            preset = get_preset(pid)
            if preset is not None:
                return resolve_settings(
                    settings=preset.settings,
                    allowed_formats=allowed_send_formats,
                )
        return resolve_settings(
            settings=initial,
            send_format=current_fmt_key(),
            allowed_formats=allowed_send_formats,
        )

    def set_settings(settings: AudioEncodeSettings) -> None:
        s = resolve_settings(
            settings=settings, allowed_formats=allowed_send_formats
        )
        fmt = s.normalized_format()
        if fmt == "aac" and "m4a" in fmt_keys and "aac" not in fmt_keys:
            fmt = "m4a"
        if fmt not in fmt_keys:
            fmt = fmt_keys[0] if fmt_keys else "mp3"
        fmt_var.set(key_to_label.get(fmt, fmt_labels[0] if fmt_labels else "MP3"))
        refill_presets(select_id=s.preset_id)

    def on_fmt_change(_e=None) -> None:
        refill_presets()

    def set_controls_state() -> None:
        enabled = bool(override_var.get())
        state = "readonly" if enabled else DISABLED
        fmt_combo.configure(state=state)
        preset_list.configure(state=NORMAL if enabled else DISABLED)

    fmt_combo.bind("<<ComboboxSelected>>", on_fmt_change)
    override_var.trace_add("write", lambda *_a: set_controls_state())
    refill_presets(select_id=initial.preset_id)
    set_controls_state()

    # Attach helpers for per-show picker reuse.
    get_settings.set_settings = set_settings  # type: ignore[attr-defined]
    get_settings.set_enabled = lambda v: override_var.set(bool(v))  # type: ignore[attr-defined]
    get_settings.override_var = override_var  # type: ignore[attr-defined]
    return override_var, get_settings


def show_podcast_settings_dialog(
    parent,
    *,
    auto_enabled: bool = False,
    schedule_days: list[str] | tuple[str, ...] | None = None,
    schedule_time: str = DEFAULT_PODCAST_SCHEDULE_TIME,
    max_new_per_show: int = 1,
    auto_sync_to_device: bool = True,
    status_line: str = "",
    use_audiobook_encode_override: bool = False,
    audiobook_audio_encode: AudioEncodeSettings | None = None,
    use_podcast_encode_override: bool = False,
    podcast_audio_encode: AudioEncodeSettings | None = None,
    global_audio_encode: AudioEncodeSettings | None = None,
    allowed_send_formats: frozenset[str] | None = None,
    profile_display_name: str | None = None,
) -> PodcastSettingsResult | None:
    """Podcast schedule + default podcast/audiobook encode. Save → result.

    Per-show encode is edited via the Podcasts tab show context menu.

    Set ``run_full_sync_now`` when the user chooses **Full Sync Now**
    (settings are still saved first).
    """
    days0 = normalize_schedule_days(
        schedule_days if schedule_days is not None else WEEKDAY_KEYS
    )
    time0 = normalize_schedule_time(schedule_time)
    n0 = normalize_max_new_per_show(max_new_per_show)

    allowed_tuple = formats_allowed(allowed_send_formats)
    global_enc = resolve_settings(
        settings=global_audio_encode,
        allowed_formats=allowed_send_formats,
    )
    if use_audiobook_encode_override and audiobook_audio_encode is not None:
        ab_initial = resolve_settings(
            settings=audiobook_audio_encode,
            allowed_formats=allowed_send_formats,
        )
    else:
        ab_initial = resolve_settings(
            settings=default_audiobook_audio_encode_settings(),
            allowed_formats=allowed_send_formats,
        )
    if use_podcast_encode_override and podcast_audio_encode is not None:
        pod_initial = resolve_settings(
            settings=podcast_audio_encode,
            allowed_formats=allowed_send_formats,
        )
    else:
        pod_initial = resolve_settings(
            settings=default_podcast_audio_encode_settings(),
            allowed_formats=allowed_send_formats,
        )

    dlg = Toplevel(parent)
    dlg.title("Podcast Settings")
    dlg.transient(parent)
    dlg.resizable(True, True)

    # Scrollable body — dialog grows with encode sections.
    outer = Frame(dlg)
    outer.pack(fill=BOTH, expand=True)
    canvas = ttk.Frame(outer)  # plain container; use Canvas for scroll
    # Simple vertical scroll via Canvas
    from tkinter import Canvas

    scroll_canvas = Canvas(outer, highlightthickness=0)
    vscroll = Scrollbar(outer, orient="vertical", command=scroll_canvas.yview)
    scroll_canvas.configure(yscrollcommand=vscroll.set)
    vscroll.pack(side=RIGHT, fill=Y)
    scroll_canvas.pack(side=LEFT, fill=BOTH, expand=True)
    body = Frame(scroll_canvas, padx=14, pady=12)
    body_id = scroll_canvas.create_window((0, 0), window=body, anchor="nw")

    def _on_body_configure(_e=None) -> None:
        scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

    def _on_canvas_configure(e) -> None:
        scroll_canvas.itemconfigure(body_id, width=e.width)

    body.bind("<Configure>", _on_body_configure)
    scroll_canvas.bind("<Configure>", _on_canvas_configure)

    def _on_mousewheel(event) -> None:
        # macOS: event.delta is small multiples of 1; Windows: multiples of 120.
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            scroll_canvas.yview_scroll(-1 if delta > 0 else 1, "units")

    scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

    Label(
        body,
        text="Scheduled full sync",
        font=("", 11, "bold"),
        anchor="w",
    ).pack(fill="x", pady=(0, 4))
    Label(
        body,
        text=(
            "While MtpManager is open, check subscribed feeds on a schedule "
            "and download at most N episodes published since the last full "
            "sync (never older catalog fillers). Missed times catch up after "
            "launch or wake. Use Full Sync "
            "Now to run the same pass immediately."
        ),
        justify=LEFT,
        wraplength=440,
    ).pack(anchor="w", pady=(0, 10))

    enabled_var = BooleanVar(value=bool(auto_enabled))
    Checkbutton(
        body,
        text="Enable scheduled full sync",
        variable=enabled_var,
        anchor="w",
    ).pack(fill="x", pady=(0, 8))

    Label(body, text="Days:", anchor="w").pack(fill="x")
    days_frame = Frame(body)
    days_frame.pack(fill="x", pady=(2, 6))
    day_vars: dict[str, BooleanVar] = {}
    labels = (
        ("mon", "Mon"),
        ("tue", "Tue"),
        ("wed", "Wed"),
        ("thu", "Thu"),
        ("fri", "Fri"),
        ("sat", "Sat"),
        ("sun", "Sun"),
    )
    for i, (key, lab) in enumerate(labels):
        v = BooleanVar(value=key in days0)
        day_vars[key] = v
        Checkbutton(days_frame, text=lab, variable=v).grid(
            row=0, column=i, sticky="w", padx=(0, 4)
        )

    preset_row = Frame(body)
    preset_row.pack(fill="x", pady=(0, 8))

    def set_days(keys: tuple[str, ...]) -> None:
        want = set(keys)
        for k, var in day_vars.items():
            var.set(k in want)

    Button(
        preset_row,
        text="Weekdays",
        width=10,
        command=lambda: set_days(WEEKDAY_KEYS),
    ).pack(side=LEFT, padx=(0, 4))
    Button(
        preset_row,
        text="Daily",
        width=10,
        command=lambda: set_days(ALL_DAY_KEYS),
    ).pack(side=LEFT)

    Label(body, text="Time (local):", anchor="w").pack(fill="x", pady=(4, 2))
    time_frame, get_time = _time_spinner_row(body, initial_hhmm=time0)
    time_frame.pack(anchor="w", pady=(0, 10))

    n_row = Frame(body)
    n_row.pack(fill="x", pady=(0, 8))
    Label(
        n_row,
        text=f"Max new episodes per show (1–{MAX_PODCAST_NEW_PER_SHOW}):",
    ).pack(side=LEFT)
    n_var = StringVar(value=str(n0))
    n_entry = Entry(n_row, textvariable=n_var, width=4)
    n_entry.pack(side=LEFT, padx=(8, 0))

    n_btn_col = Frame(n_row)
    n_btn_col.pack(side=LEFT, padx=(4, 0))

    def bump_n(delta: int) -> None:
        try:
            cur = int(str(n_var.get()).strip() or "1")
        except ValueError:
            cur = 1
        n_var.set(str(normalize_max_new_per_show(cur + delta)))

    Button(n_btn_col, text="▲", width=2, command=lambda: bump_n(1)).pack()
    Button(n_btn_col, text="▼", width=2, command=lambda: bump_n(-1)).pack()

    sync_var = BooleanVar(value=bool(auto_sync_to_device))
    Checkbutton(
        body,
        text="Sync to device when connected (after full sync)",
        variable=sync_var,
        anchor="w",
    ).pack(fill="x", pady=(4, 2))

    if status_line:
        Label(
            body,
            text=status_line,
            justify=LEFT,
            wraplength=440,
            fg="#444",
        ).pack(anchor="w", pady=(6, 10))

    # ---- Podcast encode (default for all shows; overrides Config) ----
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(10, 10))
    pod_override_var, pod_get_settings = _pack_encode_override_section(
        body,
        title="Podcast encode",
        blurb=(
            "Optional default for podcast episodes (genre Podcast). "
            "Overrides Config → Config… "
            f"({global_enc.summary_line()}) when enabled. "
            "Per-show overrides: right-click a show on the Podcasts tab → "
            "Encode Settings…"
        ),
        use_override=bool(use_podcast_encode_override),
        initial=pod_initial,
        allowed_send_formats=allowed_send_formats,
        checkbox_text="Use custom encode for podcasts",
        list_height=4,
    )

    # ---- Audiobook encode (overrides Config for Audiobooks tab only) ----
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(10, 10))
    ab_override_var, ab_get_settings = _pack_encode_override_section(
        body,
        title="Audiobook encode",
        blurb=(
            "Optional override for tracks under the Audiobooks tab (genre "
            "Audiobook). Music still uses Config → Config… "
            f"({global_enc.summary_line()})."
        ),
        use_override=bool(use_audiobook_encode_override),
        initial=ab_initial,
        allowed_send_formats=allowed_send_formats,
        checkbox_text="Use custom encode for audiobooks",
        list_height=4,
    )

    if allowed_send_formats is not None:
        names = ", ".join(format_display_name(f) for f in allowed_tuple)
        who = profile_display_name or "this device"
        Label(
            body,
            text=f"Formats limited by {who}: {names}.",
            justify=LEFT,
            wraplength=440,
            fg="#555",
        ).pack(anchor="w", pady=(2, 0))

    result: list[PodcastSettingsResult | None] = [None]

    def build_result(*, run_now: bool) -> PodcastSettingsResult | None:
        chosen_days = [k for k, v in day_vars.items() if v.get()]
        if not chosen_days:
            messagebox.showerror(
                "Podcast Settings",
                "Select at least one day.",
                parent=dlg,
            )
            return None
        try:
            n = normalize_max_new_per_show(int(str(n_var.get()).strip() or "1"))
        except (TypeError, ValueError):
            messagebox.showerror(
                "Podcast Settings",
                f"Max episodes must be a number between 1 and {MAX_PODCAST_NEW_PER_SHOW}.",
                parent=dlg,
            )
            return None
        use_ab = bool(ab_override_var.get())
        ab_settings = ab_get_settings() if use_ab else None
        use_pod = bool(pod_override_var.get())
        pod_settings = pod_get_settings() if use_pod else None
        return PodcastSettingsResult(
            auto_enabled=bool(enabled_var.get()),
            schedule_days=tuple(normalize_schedule_days(chosen_days)),
            schedule_time=get_time(),
            max_new_per_show=n,
            auto_sync_to_device=bool(sync_var.get()),
            run_full_sync_now=run_now,
            use_audiobook_encode_override=use_ab,
            audiobook_audio_encode=ab_settings,
            use_podcast_encode_override=use_pod,
            podcast_audio_encode=pod_settings,
        )

    def _cleanup_bindings() -> None:
        try:
            scroll_canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass

    def on_save() -> None:
        built = build_result(run_now=False)
        if built is None:
            return
        result[0] = built
        _cleanup_bindings()
        dlg.destroy()

    def on_full_sync() -> None:
        built = build_result(run_now=True)
        if built is None:
            return
        result[0] = built
        _cleanup_bindings()
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        _cleanup_bindings()
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(8, 0))
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Save", width=10, command=on_save).pack(side=RIGHT)
    Button(
        btn_row, text="Full Sync Now", width=14, command=on_full_sync
    ).pack(side=LEFT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 500) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 640) // 3)
        dlg.geometry(f"520x640+{px}+{py}")
    except Exception:
        dlg.geometry("520x640")
    parent.wait_window(dlg)
    return result[0]


def show_podcast_show_encode_dialog(
    parent,
    *,
    show_title: str,
    use_override: bool = False,
    audio_encode: AudioEncodeSettings | None = None,
    inherit_summary: str = "",
    allowed_send_formats: frozenset[str] | None = None,
    profile_display_name: str | None = None,
) -> PodcastShowEncodeResult | None:
    """Per-show encode override (Podcasts tab context menu). Save → result."""
    title = (show_title or "Podcast").strip() or "Podcast"
    inherit = (inherit_summary or "").strip() or "podcast default / Config"
    if use_override and audio_encode is not None:
        initial = resolve_settings(
            settings=audio_encode,
            allowed_formats=allowed_send_formats,
        )
    else:
        initial = resolve_settings(
            settings=default_podcast_audio_encode_settings(),
            allowed_formats=allowed_send_formats,
        )

    dlg = Toplevel(parent)
    dlg.title(f"Encode — {title}")
    dlg.transient(parent)
    dlg.resizable(False, False)

    body = Frame(dlg, padx=14, pady=12)
    body.pack(fill=BOTH, expand=True)

    Label(
        body,
        text=title,
        font=("", 11, "bold"),
        anchor="w",
    ).pack(fill="x", pady=(0, 4))
    Label(
        body,
        text=(
            "Optional encode recipe for this show only (e.g. talk shows "
            "heavily compressed; music/RPG shows higher quality). When off, "
            f"episodes use: {inherit}."
        ),
        justify=LEFT,
        wraplength=420,
        fg="#333",
    ).pack(anchor="w", pady=(0, 8))

    override_var, get_settings = _pack_encode_override_section(
        body,
        title="",
        blurb="",
        use_override=bool(use_override),
        initial=initial,
        allowed_send_formats=allowed_send_formats,
        checkbox_text="Use custom encode for this show",
        list_height=6,
    )

    allowed_tuple = formats_allowed(allowed_send_formats)
    if allowed_send_formats is not None:
        names = ", ".join(format_display_name(f) for f in allowed_tuple)
        who = profile_display_name or "this device"
        Label(
            body,
            text=f"Formats limited by {who}: {names}.",
            justify=LEFT,
            wraplength=420,
            fg="#555",
        ).pack(anchor="w", pady=(4, 0))

    result: list[PodcastShowEncodeResult | None] = [None]

    def on_save() -> None:
        use = bool(override_var.get())
        result[0] = PodcastShowEncodeResult(
            use_override=use,
            audio_encode=get_settings() if use else None,
        )
        dlg.destroy()

    def on_cancel() -> None:
        result[0] = None
        dlg.destroy()

    btn_row = Frame(body)
    btn_row.pack(fill="x", pady=(12, 0))
    Button(btn_row, text="Cancel", width=10, command=on_cancel).pack(
        side=RIGHT, padx=(6, 0)
    )
    Button(btn_row, text="Save", width=10, command=on_save).pack(side=RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.grab_set()
    try:
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - 460) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - 420) // 3)
        dlg.geometry(f"+{px}+{py}")
    except Exception:
        pass
    parent.wait_window(dlg)
    return result[0]

