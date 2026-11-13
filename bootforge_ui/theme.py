"""Theme helpers, user preferences persistence, and formatting utilities for BootForge."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")

from gi.repository import Adw, Gdk, GLib, Gtk

APP_CSS = """
.bf-mono {
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-variant-numeric: tabular-nums;
}

.bf-device-bar {
  background-color: alpha(@window_fg_color, 0.03);
  border-bottom: 1px solid alpha(@window_fg_color, 0.08);
  padding: 8px 16px;
}

.bf-statusbar {
  background-color: alpha(@window_fg_color, 0.03);
  border-top: 1px solid alpha(@window_fg_color, 0.08);
  padding: 8px 16px;
}

.bf-step-badge {
  min-width: 24px;
  min-height: 24px;
  border-radius: 999px;
  background-color: @accent_bg_color;
  color: @accent_fg_color;
  font-weight: 700;
  font-size: 12px;
}

.bf-dropzone {
  border: 1.5px dashed alpha(@window_fg_color, 0.25);
  border-radius: 10px;
  background-color: alpha(@window_fg_color, 0.025);
  transition: border-color 160ms ease, background-color 160ms ease;
}

.bf-dropzone.bf-drag-hover {
  border-color: @accent_bg_color;
  background-color: alpha(@accent_bg_color, 0.12);
}

.bf-terminal-wrap {
  background-color: #101216;
  border-top: 1px solid alpha(@window_fg_color, 0.10);
}

.bf-terminal-view,
.bf-terminal-view text {
  background-color: #101216;
  color: #cbd5e1;
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 11.5px;
}

.bf-code-view,
.bf-code-view text {
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 12px;
}

.bf-status-pill {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11.5px;
  font-weight: 600;
}

.bf-pill-ok {
  background-color: alpha(#22c55e, 0.16);
  color: #22c55e;
}

.bf-pill-warn {
  background-color: alpha(#f59e0b, 0.16);
  color: #f59e0b;
}

.bf-pill-err {
  background-color: alpha(#ef4444, 0.16);
  color: #ef4444;
}

.bf-pill-cyan {
  background-color: alpha(@accent_bg_color, 0.16);
  color: @accent_color;
}

.bf-pill-neutral {
  background-color: alpha(@window_fg_color, 0.08);
  color: alpha(@window_fg_color, 0.78);
}

.bf-part-legend-dot {
  min-width: 9px;
  min-height: 9px;
  border-radius: 3px;
}

.bf-dot-amber { background-color: #f59e0b; }
.bf-dot-cyan { background-color: #06b6d4; }
.bf-dot-emerald { background-color: #10b981; }
.bf-dot-violet { background-color: #8b5cf6; }
.bf-dot-slate { background-color: #64748b; }
"""


def install_css() -> None:
    display = Gdk.Display.get_default()
    if not display:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(APP_CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def esc(text: str) -> str:
    """Escape plain text so Pango markup properties never fail on '&' or '<'."""
    return GLib.markup_escape_text(str(text or ""))


def _prefs_file() -> Path:
    cfg_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return cfg_home / "bootforge" / "settings.json"


def load_prefs() -> Dict[str, Any]:
    path = _prefs_file()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"mode": "simple"}


def save_prefs(prefs: Dict[str, Any]) -> None:
    path = _prefs_file()
    try:
        cur = load_prefs()
        if not isinstance(cur, dict):
            cur = {}
        cur.update(prefs)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def format_bytes(size_bytes: int | float) -> str:
    if not size_bytes or size_bytes <= 0:
        return "0 o"
    units = ["o", "Ko", "Mo", "Go", "To"]
    idx = 0
    val = float(size_bytes)
    while val >= 1024.0 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    if idx <= 1:
        return f"{val:.0f} {units[idx]}"
    return f"{val:.1f} {units[idx]}" if idx == 2 else f"{val:.2f} {units[idx]}"


def format_speed(bps: float | None) -> str:
    if not bps or bps <= 0:
        return ""
    mbps = bps / (1024.0 * 1024.0)
    if mbps >= 1.0:
        return f"{mbps:.1f} Mo/s"
    return f"{bps / 1024.0:.0f} Ko/s"


def make_icon_button(label: str, icon_name: str, css_classes: tuple[str, ...] = ()) -> Gtk.Button:
    """Create a Gtk.Button that renders both an icon and a visible text label."""
    btn = Gtk.Button()
    content = Adw.ButtonContent(label=label, icon_name=icon_name)
    btn.set_child(content)
    for cls in css_classes:
        btn.add_css_class(cls)
    return btn


def set_button_text(btn: Gtk.Button, label: str) -> None:
    """Update a button's text without losing its Adw.ButtonContent icon."""
    child = btn.get_child()
    if isinstance(child, Adw.ButtonContent):
        child.set_label(label)
    else:
        btn.set_label(label)

