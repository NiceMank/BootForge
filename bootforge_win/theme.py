"""Theme styling, settings persistence, and QPainter PartitionMap for BootForge Windows UI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def settings_file_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "BootForge" / "settings.json"


def load_win_prefs() -> Dict[str, Any]:
    path = settings_file_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"mode": "simple", "theme": "dark"}


def save_win_prefs(prefs: Dict[str, Any]) -> None:
    path = settings_file_path()
    try:
        cur = load_win_prefs()
        cur.update(prefs)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def format_bytes(num_bytes: int | float) -> str:
    n = max(float(num_bytes or 0), 0.0)
    units = ("o", "Ko", "Mo", "Go", "To")
    idx = 0
    while n >= 1024.0 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(n)} {units[idx]}"
    return f"{n:.2f} {units[idx]}"


def format_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    mbps = bps / (1024.0 * 1024.0)
    return f"{mbps:.1f} Mo/s" if mbps >= 0.1 else f"{bps / 1024.0:.0f} Ko/s"


def build_qss(dark: bool) -> str:
    if dark:
        bg = "#1B1D24"
        surface = "#23262F"
        surface_alt = "#2B2F3B"
        topbar = "#15171D"
        border = "#343946"
        fg = "#F1F3F7"
        muted = "#9BA3B4"
        input_bg = "#16181E"
        console_bg = "#0E1015"
    else:
        bg = "#F3F5F8"
        surface = "#FFFFFF"
        surface_alt = "#EAEEF4"
        topbar = "#FFFFFF"
        border = "#D5DBE5"
        fg = "#161922"
        muted = "#5A6375"
        input_bg = "#F8FAFC"
        console_bg = "#151821"

    accent = "#D92654"
    accent_hover = "#EA3564"
    accent_pressed = "#B81E45"

    return f"""
    QMainWindow, QDialog {{
        background-color: {bg};
        color: {fg};
        font-family: "Segoe UI", "Inter", "Cantarell", sans-serif;
        font-size: 13px;
    }}
    QWidget {{
        color: {fg};
        font-family: "Segoe UI", "Inter", "Cantarell", sans-serif;
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QFrame#TopHeader {{
        background-color: {topbar};
        border-bottom: 1px solid {border};
    }}
    QFrame#DeviceBar {{
        background-color: {surface};
        border-bottom: 1px solid {border};
    }}
    QFrame#StatusBar {{
        background-color: {topbar};
        border-top: 1px solid {border};
    }}
    QFrame#ConsoleWrap {{
        background-color: {console_bg};
        border-top: 1px solid {border};
    }}
    QFrame#Card {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
    }}
    QFrame#DropZone {{
        background-color: {surface_alt};
        border: 1.5px dashed {accent};
        border-radius: 10px;
    }}
    QLabel#GroupTitle {{
        font-size: 14px;
        font-weight: 700;
        color: {fg};
    }}
    QLabel#GroupSub, QLabel#Muted {{
        font-size: 12px;
        color: {muted};
    }}
    QLabel#MonoLabel {{
        font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
        font-size: 12px;
    }}
    QPushButton {{
        background-color: {surface_alt};
        border: 1px solid {border};
        border-radius: 7px;
        padding: 6px 13px;
        font-weight: 600;
        color: {fg};
    }}
    QPushButton:hover {{
        border-color: {muted};
        background-color: {border};
    }}
    QPushButton:disabled {{
        opacity: 0.45;
        color: {muted};
    }}
    QPushButton#NavTab {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px 12px;
        font-weight: 600;
        color: {muted};
    }}
    QPushButton#NavTab:hover {{
        background-color: {surface_alt};
        color: {fg};
    }}
    QPushButton#NavTab:checked {{
        background-color: {surface_alt};
        border: 1px solid {border};
        color: {fg};
    }}
    QPushButton#ModeBtn {{
        background-color: {surface_alt};
        border: 1px solid {border};
        padding: 5px 12px;
        font-weight: 600;
        color: {muted};
    }}
    QPushButton#ModeBtnLeft {{
        border-top-left-radius: 7px;
        border-bottom-left-radius: 7px;
        border-top-right-radius: 0px;
        border-bottom-right-radius: 0px;
    }}
    QPushButton#ModeBtnRight {{
        border-top-right-radius: 7px;
        border-bottom-right-radius: 7px;
        border-top-left-radius: 0px;
        border-bottom-left-radius: 0px;
        border-left: none;
    }}
    QPushButton#ModeBtnLeft:checked, QPushButton#ModeBtnRight:checked {{
        background-color: {muted if dark else "#4B5565"};
        color: #FFFFFF;
    }}
    QPushButton#PrimaryBtn {{
        background-color: {accent};
        border: 1px solid {accent};
        color: #FFFFFF;
        font-weight: 700;
        padding: 7px 16px;
        border-radius: 7px;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {accent_hover};
        border-color: {accent_hover};
    }}
    QPushButton#PrimaryBtn:pressed {{
        background-color: {accent_pressed};
    }}
    QPushButton#PrimaryBtn:disabled {{
        background-color: {surface_alt};
        border-color: {border};
        color: {muted};
    }}
    QPushButton#DangerBtn {{
        background-color: rgba(239, 68, 68, 0.16);
        border: 1px solid rgba(239, 68, 68, 0.48);
        color: #F87171;
        font-weight: 600;
    }}
    QPushButton#DangerBtn:hover {{
        background-color: #DC2626;
        color: #FFFFFF;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {input_bg};
        border: 1px solid {border};
        border-radius: 7px;
        padding: 5px 10px;
        min-height: 22px;
        color: {fg};
    }}
    QPlainTextEdit#ConsoleText, QPlainTextEdit#CodeView {{
        background-color: {console_bg};
        color: #D7DCE5;
        border: none;
        font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
        font-size: 12px;
        padding: 8px;
    }}
    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border-radius: 4px;
        border: 1px solid {border};
        background-color: {input_bg};
    }}
    QCheckBox::indicator:checked {{
        background-color: {accent};
        border-color: {accent};
    }}
    QCheckBox#SwitchToggle::indicator {{
        width: 38px;
        height: 20px;
        border-radius: 10px;
        border: 1px solid {border};
        background-color: {surface_alt};
    }}
    QCheckBox#SwitchToggle::indicator:checked {{
        background-color: {accent};
        border-color: {accent};
    }}
    QProgressBar {{
        background-color: {surface_alt};
        border: 1px solid {border};
        border-radius: 5px;
        height: 10px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {accent};
        border-radius: 4px;
    }}
    """


class PartitionMapWidget(QWidget):
    """QPainter hardware partition layout diagram matching Linux BootForge."""

    COLOR_MAP = {
        "amber": QColor(245, 158, 11),
        "cyan": QColor(6, 182, 212),
        "emerald": QColor(34, 197, 94),
        "violet": QColor(168, 85, 247),
        "slate": QColor(113, 122, 140),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: List[Dict[str, Any]] = []
        self._title = "Schéma de partitionnement prévu"
        self._badge = "GPT · UEFI"
        self._dark = True
        self.setMinimumHeight(88)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def set_layout(self, segments: List[Dict[str, Any]], title: str = "", badge: str = "") -> None:
        self._segments = list(segments or [])
        if title:
            self._title = title
        if badge:
            self._badge = badge
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        if w <= 20 or h <= 20:
            return

        fg = QColor("#E8ECF4" if self._dark else "#181C24")
        muted = QColor("#9BA3B4" if self._dark else "#5A6375")
        border = QColor("#343946" if self._dark else "#CFD6E2")
        bar_bg = QColor("#151821" if self._dark else "#EAEFF5")

        f_title = QFont(self.font())
        f_title.setBold(True)
        f_title.setPointSizeF(9.5)
        painter.setFont(f_title)
        painter.setPen(fg)
        painter.drawText(QRectF(0, 0, w - 130, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)

        f_badge = QFont(self.font())
        f_badge.setPointSizeF(8.5)
        painter.setFont(f_badge)
        painter.setPen(muted)
        painter.drawText(QRectF(0, 0, w, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._badge)

        bar_y = 24.0
        bar_h = 30.0
        bar_rect = QRectF(0, bar_y, w, bar_h)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(bar_rect, 7, 7)
        painter.fillPath(bg_path, bar_bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(bg_path)

        total_mib = sum(max(int(s.get("size_mib") or 0), 1) for s in self._segments) or 1
        x_cur = 2.0
        usable_w = max(w - 4.0, 20.0)
        painter.setClipPath(bg_path)

        for idx, seg in enumerate(self._segments):
            mib = max(int(seg.get("size_mib") or 0), 1)
            raw_w = (mib / total_mib) * usable_w
            min_w = 54.0 if len(self._segments) > 1 else usable_w
            seg_w = max(raw_w, min_w) if idx == len(self._segments) - 1 else raw_w
            if idx == len(self._segments) - 1:
                seg_w = max(usable_w - (x_cur - 2.0), 18.0)

            col = self.COLOR_MAP.get(str(seg.get("color", "cyan")), self.COLOR_MAP["cyan"])
            fill = QColor(col.red(), col.green(), col.blue(), 68)
            stroke = QColor(col.red(), col.green(), col.blue(), 220)

            srect = QRectF(x_cur + 1, bar_y + 2, max(seg_w - 3, 8.0), bar_h - 4)
            spath = QPainterPath()
            spath.addRoundedRect(srect, 5, 5)
            painter.fillPath(spath, fill)
            painter.setPen(QPen(stroke, 1.1))
            painter.drawPath(spath)

            if srect.width() >= 48:
                sz_txt = f"{mib}M" if mib < 1024 else f"{mib / 1024:.1f}G"
                lbl = f"{seg.get('label', '')} · {sz_txt}" if srect.width() >= 130 else str(seg.get("label", ""))
                f_seg = QFont(self.font())
                f_seg.setBold(True)
                f_seg.setPointSizeF(8.5)
                painter.setFont(f_seg)
                painter.setPen(fg)
                elided = painter.fontMetrics().elidedText(lbl, Qt.TextElideMode.ElideRight, int(srect.width() - 10))
                painter.drawText(srect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignCenter, elided)

            x_cur += seg_w

        painter.setClipping(False)

        # Legend row
        lx = 2.0
        ly = bar_y + bar_h + 7.0
        f_leg = QFont(self.font())
        f_leg.setPointSizeF(8.5)
        painter.setFont(f_leg)
        for seg in self._segments:
            col = self.COLOR_MAP.get(str(seg.get("color", "cyan")), self.COLOR_MAP["cyan"])
            painter.setBrush(col)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(lx, ly + 4, 7, 7))
            lx += 11
            mib = int(seg.get("size_mib") or 0)
            sz_txt = f"{mib} Mo" if mib < 1024 else f"{mib / 1024:.1f} Go"
            txt = f"{seg.get('label', '')} ({str(seg.get('filesystem', '')).upper()} {sz_txt})"
            painter.setPen(muted)
            tw = painter.fontMetrics().horizontalAdvance(txt)
            if lx + tw > w - 4:
                break
            painter.drawText(QRectF(lx, ly, tw + 6, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
            lx += tw + 16
