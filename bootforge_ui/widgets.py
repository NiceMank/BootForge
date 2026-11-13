"""Native GTK4 / Libadwaita / Cairo widgets and dialogs for BootForge."""

from __future__ import annotations

import math
import threading
from typing import Any, Callable, Dict, List, Optional

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, PangoCairo

from engine.i18n import get_current_language, t
from engine.unattend_builder import build_autounattend_xml
from engine.updater import (
    UpdateCheckResult,
    apply_downloaded_update,
    check_for_update,
    download_verified_update,
    get_current_version,
)
from bootforge_ui.theme import esc, format_bytes, load_prefs, make_icon_button, save_prefs


COLOR_PALETTE = {
    "amber": (0.96, 0.62, 0.04),
    "cyan": (0.02, 0.71, 0.83),
    "emerald": (0.06, 0.73, 0.51),
    "violet": (0.55, 0.36, 0.96),
    "slate": (0.39, 0.45, 0.55),
}


def _rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2.0, h / 2.0)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class PartitionMapWidget(Gtk.Box):
    """Vector partition visualizer used in Advanced mode."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._segments: List[Dict[str, Any]] = []
        self._rects: List[tuple[float, float, float, float, Dict[str, Any]]] = []

        self._title_lbl = Gtk.Label(xalign=0)
        self._title_lbl.add_css_class("heading")
        self._badge_lbl = Gtk.Label(xalign=1)
        self._badge_lbl.add_css_class("bf-status-pill")
        self._badge_lbl.add_css_class("bf-pill-neutral")
        self._badge_lbl.add_css_class("bf-mono")

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr.append(self._title_lbl)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        hdr.append(spacer)
        hdr.append(self._badge_lbl)
        self.append(hdr)

        self._drawing = Gtk.DrawingArea()
        self._drawing.set_content_height(44)
        self._drawing.set_draw_func(self._on_draw)
        self._drawing.set_has_tooltip(True)
        self._drawing.connect("query-tooltip", self._on_tooltip)
        self.append(self._drawing)

        self._legend_box = Gtk.FlowBox()
        self._legend_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._legend_box.set_max_children_per_line(6)
        self._legend_box.set_column_spacing(12)
        self._legend_box.set_row_spacing(4)
        self.append(self._legend_box)

        self.set_layout([], title="Schéma de partitionnement cible", badge="GPT")

    def set_layout(self, segments: List[Dict[str, Any]], title: str = "", badge: str = "") -> None:
        self._segments = list(segments or [])
        if title:
            self._title_lbl.set_label(title)
        self._badge_lbl.set_label(badge or "GPT")

        while child := self._legend_box.get_first_child():
            self._legend_box.remove(child)

        for seg in self._segments:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            dot = Gtk.Box()
            dot.add_css_class("bf-part-legend-dot")
            dot.add_css_class(f"bf-dot-{seg.get('color', 'slate')}")
            dot.set_valign(Gtk.Align.CENTER)
            row.append(dot)

            lbl = Gtk.Label(label=str(seg.get("label") or seg.get("id") or "Partition"))
            lbl.add_css_class("caption")
            lbl.add_css_class("heading")
            row.append(lbl)

            fs = Gtk.Label(label=str(seg.get("filesystem") or "").upper())
            fs.add_css_class("caption")
            fs.add_css_class("bf-mono")
            fs.add_css_class("dim-label")
            row.append(fs)

            size_mib = int(seg.get("size_mib") or 0)
            sz_txt = f"{size_mib / 1024:.1f} Go" if size_mib >= 1024 else f"{size_mib} Mo"
            sz = Gtk.Label(label=sz_txt)
            sz.add_css_class("caption")
            sz.add_css_class("bf-mono")
            sz.add_css_class("dim-label")
            row.append(sz)

            self._legend_box.append(row)

        self._drawing.queue_draw()

    def _on_tooltip(self, _widget: Gtk.Widget, x: int, y: int, _kb: bool, tooltip: Gtk.Tooltip) -> bool:
        for rx, ry, rw, rh, seg in self._rects:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                size_mib = int(seg.get("size_mib") or 0)
                sz_txt = f"{size_mib / 1024:.2f} Go" if size_mib >= 1024 else f"{size_mib} Mo"
                tooltip.set_text(
                    f"{seg.get('label', '')}\n"
                    f"Format : {str(seg.get('filesystem', '')).upper()} · Taille : {sz_txt}"
                )
                return True
        return False

    def _on_draw(self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int) -> None:
        self._rects.clear()
        if width < 20 or height < 10:
            return

        is_dark = Adw.StyleManager.get_default().get_dark()
        bg_track = (0.12, 0.13, 0.16, 1.0) if is_dark else (0.90, 0.92, 0.95, 1.0)
        border_track = (1.0, 1.0, 1.0, 0.12) if is_dark else (0.06, 0.09, 0.16, 0.14)
        text_fg = (0.96, 0.97, 0.99, 0.95) if is_dark else (0.08, 0.10, 0.15, 0.95)

        pad = 2.0
        track_w = max(width - pad * 2, 10.0)
        track_h = max(height - pad * 2, 10.0)

        _rounded_rect(cr, pad, pad, track_w, track_h, 8.0)
        cr.set_source_rgba(*bg_track)
        cr.fill_preserve()
        cr.set_source_rgba(*border_track)
        cr.set_line_width(1.0)
        cr.stroke()

        if not self._segments:
            return

        gap = 4.0
        avail_w = max(track_w - gap * (len(self._segments) + 1), 10.0)
        total_mib = max(sum(max(int(s.get("size_mib") or 0), 1) for s in self._segments), 1)

        raw_widths: List[float] = []
        for s in self._segments:
            mib = max(int(s.get("size_mib") or 0), 1)
            prop = (mib / total_mib) * avail_w
            min_w = 96.0 if s.get("role") == "boot" else 68.0
            raw_widths.append(max(prop, min(min_w, avail_w * 0.28)))

        scale = avail_w / max(sum(raw_widths), 1.0)
        final_widths = [w * scale for w in raw_widths]

        cursor_x = pad + gap
        slice_y = pad + 4.0
        slice_h = max(track_h - 8.0, 14.0)

        for seg, sw in zip(self._segments, final_widths):
            color_key = str(seg.get("color") or "slate")
            r, g, b = COLOR_PALETTE.get(color_key, COLOR_PALETTE["slate"])
            role = seg.get("role")

            cr.save()
            _rounded_rect(cr, cursor_x, slice_y, sw, slice_h, 6.0)
            cr.set_source_rgba(r, g, b, 0.22 if is_dark else 0.16)
            cr.fill_preserve()

            if role == "reserved":
                cr.save()
                cr.clip_preserve()
                cr.set_source_rgba(r, g, b, 0.20)
                cr.set_line_width(1.4)
                step = 9.0
                hx = cursor_x - slice_h
                while hx < cursor_x + sw + slice_h:
                    cr.move_to(hx, slice_y + slice_h)
                    cr.line_to(hx + slice_h, slice_y)
                    hx += step
                cr.stroke()
                cr.restore()

            cr.set_source_rgba(r, g, b, 0.80)
            cr.set_line_width(1.2)
            cr.stroke()

            _rounded_rect(cr, cursor_x, slice_y, sw, slice_h, 6.0)
            cr.clip()

            layout = PangoCairo.create_layout(cr)
            size_mib = int(seg.get("size_mib") or 0)
            sz_txt = f"{size_mib / 1024:.1f}G" if size_mib >= 1024 else f"{size_mib}M"
            caption = str(seg.get("label") or seg.get("id") or "")
            text_str = caption if sw < 140 else f"{caption} · {sz_txt}"

            layout.set_text(text_str, -1)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_width(int(max(sw - 14.0, 10.0) * Pango.SCALE))
            layout.set_font_description(Pango.FontDescription.from_string("Sans Semi-Bold 9.5"))

            _, logical = layout.get_pixel_extents()
            cr.set_source_rgba(*text_fg)
            cr.move_to(cursor_x + 8.0, slice_y + (slice_h - logical.height) / 2.0)
            PangoCairo.show_layout(cr, layout)
            cr.restore()

            self._rects.append((cursor_x, slice_y, sw, slice_h, seg))
            cursor_x += sw + gap


class WinTweaksDialog(Adw.Dialog):
    """Advanced dialog for Windows 11 autounattend.xml options, local admin account, and XML preview."""

    def __init__(self, initial_options: Dict[str, Any], on_apply: Callable[[Dict[str, Any]], None]) -> None:
        super().__init__()
        self.set_title("Paramètres Windows 11 (autounattend.xml)")
        self.set_content_width(680)
        self.set_content_height(660)

        self._opts = dict(initial_options)
        self._on_apply = on_apply

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self._switcher = Adw.ViewSwitcher()
        self._switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self._switcher)

        self._apply_btn = Gtk.Button(label="Valider")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._handle_save)
        header.pack_end(self._apply_btn)
        toolbar_view.add_top_bar(header)

        self._stack = Adw.ViewStack()
        self._switcher.set_stack(self._stack)

        opts_scroll = Gtk.ScrolledWindow()
        opts_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        opts_clamp = Adw.Clamp(maximum_size=620, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        opts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        grp_bypass = Adw.PreferencesGroup(
            title=esc("Contournements Windows 11 (24H2 / 25H2)"),
            description=esc("Paramètres injectés automatiquement dans autounattend.xml."),
        )
        self._sw_enabled = Adw.SwitchRow(
            use_markup=False,
            title="Activer le fichier autounattend.xml",
            subtitle="Désactivez pour copier l’image Windows d’origine sans modification",
        )
        self._sw_enabled.set_active(bool(self._opts.get("enabled", True)))

        self._sw_hw = Adw.SwitchRow(
            use_markup=False,
            title="Ignorer TPM 2.0, Secure Boot, RAM et CPU",
            subtitle="Permet d’installer Windows 11 sur des PC non compatibles",
        )
        self._sw_hw.set_active(bool(self._opts.get("bypass_tpm_secureboot_ram", True)))

        self._sw_nro = Adw.SwitchRow(
            use_markup=False,
            title="Autoriser l’installation sans compte Microsoft",
            subtitle="Active l’installation hors ligne avec un compte local",
        )
        self._sw_nro.set_active(bool(self._opts.get("bypass_microsoft_account", True)))

        self._sw_bitlocker = Adw.SwitchRow(
            use_markup=False,
            title="Désactiver le chiffrement BitLocker automatique",
            subtitle="Évite le chiffrement du disque dès le premier démarrage",
        )
        self._sw_bitlocker.set_active(bool(self._opts.get("disable_bitlocker", True)))

        self._sw_telemetry = Adw.SwitchRow(
            use_markup=False,
            title="Désactiver les questions de confidentialité",
            subtitle="Passe les écrans de suivi et le CLUF au démarrage",
        )
        self._sw_telemetry.set_active(bool(self._opts.get("disable_telemetry", True)))

        self._sw_bloat = Adw.SwitchRow(
            use_markup=False,
            title="Désactiver les applications suggérées et Copilot",
            subtitle="Empêche l’installation automatique d’applications promotionnelles",
        )
        self._sw_bloat.set_active(bool(self._opts.get("disable_consumer_bloat", False)))

        self._sw_key = Adw.SwitchRow(
            use_markup=False,
            title="Passer la saisie de clé de produit",
            subtitle="La licence pourra être activée après l’installation",
        )
        self._sw_key.set_active(bool(self._opts.get("skip_product_key", True)))

        for w in (
            self._sw_enabled,
            self._sw_hw,
            self._sw_nro,
            self._sw_bitlocker,
            self._sw_telemetry,
            self._sw_bloat,
            self._sw_key,
        ):
            grp_bypass.add(w)
        opts_box.append(grp_bypass)

        grp_local = Adw.PreferencesGroup(
            title=esc("Compte local et langue"),
            description=esc("Crée un compte administrateur local automatiquement en fin d’installation."),
        )
        self._sw_local = Adw.SwitchRow(
            use_markup=False,
            title="Créer un compte administrateur local",
            subtitle="Aucune connexion Internet requise à la fin de l’installation",
        )
        self._sw_local.set_active(bool(self._opts.get("create_local_account", False)))

        self._entry_user = Adw.EntryRow(title="Nom d’utilisateur")
        self._entry_user.set_text(str(self._opts.get("local_username") or "Utilisateur"))

        self._entry_pass = Adw.PasswordEntryRow(title="Mot de passe (optionnel)")
        self._entry_pass.set_text(str(self._opts.get("local_password") or ""))

        self._locales = ["fr-FR", "en-US", "fr-CA", "fr-BE", "fr-CH"]
        self._locale_labels = [
            "Français (fr-FR)",
            "English (en-US)",
            "Français Canada (fr-CA)",
            "Français Belgique (fr-BE)",
            "Français Suisse (fr-CH)",
        ]
        self._combo_locale = Adw.ComboRow(use_markup=False, title="Langue et clavier")
        self._combo_locale.set_model(Gtk.StringList.new(self._locale_labels))
        cur_loc = str(self._opts.get("locale") or "fr-FR")
        self._combo_locale.set_selected(self._locales.index(cur_loc) if cur_loc in self._locales else 0)

        self._timezones = [
            "Romance Standard Time",
            "UTC",
            "W. Europe Standard Time",
            "Eastern Standard Time",
            "W. Central Africa Standard Time",
        ]
        self._combo_tz = Adw.ComboRow(use_markup=False, title="Fuseau horaire")
        self._combo_tz.set_model(Gtk.StringList.new(self._timezones))
        cur_tz = str(self._opts.get("timezone") or "Romance Standard Time")
        self._combo_tz.set_selected(self._timezones.index(cur_tz) if cur_tz in self._timezones else 0)

        for w in (self._sw_local, self._entry_user, self._entry_pass, self._combo_locale, self._combo_tz):
            grp_local.add(w)
        opts_box.append(grp_local)

        opts_clamp.set_child(opts_box)
        opts_scroll.set_child(opts_clamp)
        p_opts = self._stack.add_titled(opts_scroll, "options", "Options")
        p_opts.set_icon_name("preferences-system-symbolic")

        # XML Preview Page
        xml_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        xml_box.set_margin_top(12)
        xml_box.set_margin_bottom(12)
        xml_box.set_margin_start(14)
        xml_box.set_margin_end(14)

        xml_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        xml_hint = Gtk.Label(label="Contenu qui sera écrit dans autounattend.xml :", xalign=0)
        xml_hint.add_css_class("caption")
        xml_hint.add_css_class("dim-label")
        xml_hint.set_hexpand(True)
        xml_bar.append(xml_hint)

        copy_btn = make_icon_button("Copier", "edit-copy-symbolic")
        copy_btn.connect("clicked", self._copy_xml)
        xml_bar.append(copy_btn)
        xml_box.append(xml_bar)

        xml_scroll = Gtk.ScrolledWindow()
        xml_scroll.set_vexpand(True)
        xml_scroll.add_css_class("card")
        self._xml_view = Gtk.TextView(editable=False, cursor_visible=False)
        self._xml_view.set_monospace(True)
        self._xml_view.add_css_class("bf-code-view")
        self._xml_view.set_top_margin(10)
        self._xml_view.set_bottom_margin(10)
        self._xml_view.set_left_margin(12)
        self._xml_view.set_right_margin(12)
        xml_scroll.set_child(self._xml_view)
        xml_box.append(xml_scroll)

        self._xml_err_lbl = Gtk.Label(xalign=0)
        self._xml_err_lbl.add_css_class("error")
        self._xml_err_lbl.set_visible(False)
        xml_box.append(self._xml_err_lbl)

        p_xml = self._stack.add_titled(xml_box, "xml", "Aperçu XML")
        p_xml.set_icon_name("utilities-terminal-symbolic")

        self._stack.connect("notify::visible-child-name", self._on_tab_change)
        for sw in (self._sw_enabled, self._sw_hw, self._sw_nro, self._sw_bitlocker, self._sw_telemetry, self._sw_bloat, self._sw_key, self._sw_local):
            sw.connect("notify::active", lambda *_: self._refresh_xml_if_visible())
        self._sw_local.connect("notify::active", lambda *_: self._sync_local_sensitivity())
        self._sync_local_sensitivity()

        toolbar_view.set_content(self._stack)
        self.set_child(toolbar_view)

    def _sync_local_sensitivity(self) -> None:
        active = self._sw_local.get_active()
        self._entry_user.set_sensitive(active)
        self._entry_pass.set_sensitive(active)

    def _collect(self) -> Dict[str, Any]:
        loc_idx = self._combo_locale.get_selected()
        tz_idx = self._combo_tz.get_selected()
        return {
            "enabled": self._sw_enabled.get_active(),
            "bypass_tpm_secureboot_ram": self._sw_hw.get_active(),
            "bypass_microsoft_account": self._sw_nro.get_active(),
            "disable_bitlocker": self._sw_bitlocker.get_active(),
            "disable_telemetry": self._sw_telemetry.get_active(),
            "disable_consumer_bloat": self._sw_bloat.get_active(),
            "skip_product_key": self._sw_key.get_active(),
            "create_local_account": self._sw_local.get_active(),
            "local_username": self._entry_user.get_text().strip() or "Utilisateur",
            "local_password": self._entry_pass.get_text(),
            "locale": self._locales[loc_idx] if 0 <= loc_idx < len(self._locales) else "fr-FR",
            "timezone": self._timezones[tz_idx] if 0 <= tz_idx < len(self._timezones) else "Romance Standard Time",
        }

    def _refresh_xml(self) -> None:
        buf = self._xml_view.get_buffer()
        try:
            xml_txt = build_autounattend_xml(self._collect())
            buf.set_text(xml_txt)
            self._xml_err_lbl.set_visible(False)
        except Exception as exc:
            buf.set_text("")
            self._xml_err_lbl.set_label(str(exc))
            self._xml_err_lbl.set_visible(True)

    def _refresh_xml_if_visible(self) -> None:
        if self._stack.get_visible_child_name() == "xml":
            self._refresh_xml()

    def _on_tab_change(self, *_args: Any) -> None:
        if self._stack.get_visible_child_name() == "xml":
            self._refresh_xml()

    def _copy_xml(self, *_args: Any) -> None:
        self._refresh_xml()
        buf = self._xml_view.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if txt:
            disp = Gdk.Display.get_default()
            if disp:
                disp.get_clipboard().set(txt)

    def _handle_save(self, *_args: Any) -> None:
        data = self._collect()
        try:
            build_autounattend_xml(data)
        except Exception as exc:
            self._stack.set_visible_child_name("options")
            self._xml_err_lbl.set_label(str(exc))
            self._xml_err_lbl.set_visible(True)
            return
        self._on_apply(data)
        self.close()


class PreflightDialog(Adw.Dialog):
    """System tool availability inspector."""

    def __init__(self, deps: Optional[Dict[str, Any]], on_refresh: Callable[[], None]) -> None:
        super().__init__()
        self.set_title(t("dialog_preflight_win_title"))
        self.set_content_width(620)
        self.set_content_height(560)
        self._on_refresh = on_refresh

        toolbar = Adw.ToolbarView()
        hdr = Adw.HeaderBar()
        refresh_btn = make_icon_button(t("bar_refresh"), "view-refresh-symbolic")
        refresh_btn.connect("clicked", lambda *_: (self._on_refresh(), self.close()))
        hdr.pack_end(refresh_btn)
        toolbar.add_top_bar(hdr)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=580, margin_top=14, margin_bottom=16, margin_start=14, margin_end=14)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        if not deps:
            box.append(Gtk.Label(label="..."))
        else:
            grp_core = Adw.PreferencesGroup(
                title=esc(t("dialog_preflight_core")),
                description=esc("Présents sur votre système Linux pour créer et formater des clés USB." if get_current_language() == "fr" else "Installed on your Linux system to create and format bootable USB drives."),
            )
            for item in deps.get("core", []):
                row = Adw.ActionRow(use_markup=False, title=item["name"], subtitle=item["purpose"])
                status_txt = "OK" if item["installed"] else ("Manquant" if get_current_language() == "fr" else "Missing")
                badge = Gtk.Label(label=status_txt)
                badge.add_css_class("bf-status-pill")
                badge.add_css_class("bf-pill-ok" if item["installed"] else "bf-pill-err")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)
                grp_core.add(row)
            box.append(grp_core)

            grp_assets = Adw.PreferencesGroup(title=esc(t("dialog_preflight_win_embedded")))
            uefi_ok = bool(deps.get("uefi_ntfs_cached"))
            r_uefi = Adw.ActionRow(
                use_markup=False,
                title="Chargeur UEFI:NTFS" if get_current_language() == "fr" else "UEFI:NTFS Bootloader",
                subtitle=str(deps.get("uefi_ntfs_path") or ""),
            )
            b_txt = ("Prêt" if get_current_language() == "fr" else "Ready") if uefi_ok else ("Téléchargement auto" if get_current_language() == "fr" else "Auto-download")
            b_uefi = Gtk.Label(label=b_txt)
            b_uefi.add_css_class("bf-status-pill")
            b_uefi.add_css_class("bf-pill-ok" if uefi_ok else "bf-pill-warn")
            b_uefi.set_valign(Gtk.Align.CENTER)
            r_uefi.add_suffix(b_uefi)
            grp_assets.add(r_uefi)
            box.append(grp_assets)

            grp_opt = Adw.PreferencesGroup(
                title=esc(t("dialog_preflight_opt")),
                description=esc("Utiles pour la découpe FAT32 ou le test de démarrage virtuel." if get_current_language() == "fr" else "Useful for FAT32 splitting or virtual machine boot testing."),
            )
            for item in deps.get("optional", []):
                sub = item["purpose"]
                if not item["installed"] and item.get("install_cmd"):
                    sub = f"{sub} ({item['install_cmd']})"
                row = Adw.ActionRow(use_markup=False, title=item["name"], subtitle=sub)
                opt_txt = ("Installé" if get_current_language() == "fr" else "Installed") if item["installed"] else ("Optionnel" if get_current_language() == "fr" else "Optional")
                badge = Gtk.Label(label=opt_txt)
                badge.add_css_class("bf-status-pill")
                badge.add_css_class("bf-pill-ok" if item["installed"] else "bf-pill-neutral")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)
                grp_opt.add(row)
            box.append(grp_opt)

        clamp.set_child(box)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
        self.set_child(toolbar)


def present_confirm_destroy_dialog(
    parent: Gtk.Widget,
    device: Dict[str, Any],
    operation_label: str,
    detail_text: str,
    on_confirmed: Callable[[], None],
    simple_mode: bool = True,
) -> None:
    """Confirm before erasing a USB drive."""
    dialog = Adw.Dialog()
    dialog.set_title(t("dialog_confirm_title_simple") if simple_mode else t("dialog_confirm_title_adv"))
    dialog.set_content_width(450)

    toolbar = Adw.ToolbarView()
    hdr = Adw.HeaderBar()
    toolbar.add_top_bar(hdr)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    content.set_margin_top(14)
    content.set_margin_bottom(16)
    content.set_margin_start(18)
    content.set_margin_end(18)

    banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    warn_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
    warn_icon.set_pixel_size(28)
    warn_icon.add_css_class("warning")
    banner_box.append(warn_icon)

    intro = (
        t("dialog_confirm_intro_simple", detail=detail_text)
        if simple_mode
        else t("dialog_confirm_intro_adv", detail=detail_text)
    )
    msg = Gtk.Label(label=intro, xalign=0, wrap=True)
    banner_box.append(msg)
    content.append(banner_box)

    grp = Adw.PreferencesGroup()
    dev_sub = (
        f"{device.get('model', 'USB')} ({format_bytes(device.get('size_bytes', 0))})"
        if simple_mode
        else f"{device.get('model', 'Disk')} ({device.get('path', '')}) · {format_bytes(device.get('size_bytes', 0))}"
    )
    grp.add(Adw.ActionRow(use_markup=False, title=t("dialog_confirm_action_lbl", action="").replace(" : <b></b>", "").replace("<b>", "").replace("</b>", ""), subtitle=operation_label))
    grp.add(Adw.ActionRow(use_markup=False, title=t("app_device_label").rstrip(" :"), subtitle=dev_sub))
    content.append(grp)

    confirm_word = t("dialog_confirm_word")
    prompt_txt = t("dialog_confirm_prompt_msg", word=confirm_word).replace("<b>", "").replace("</b>", "")
    entry = Adw.EntryRow(title=prompt_txt)
    entry_grp = Adw.PreferencesGroup()
    entry_grp.add(entry)
    content.append(entry_grp)

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    btn_row.set_halign(Gtk.Align.END)

    cancel_btn = Gtk.Button(label=t("dialog_confirm_btn_cancel"))
    cancel_btn.connect("clicked", lambda *_: dialog.close())

    confirm_btn = Gtk.Button(label=t("dialog_confirm_btn_exec"))
    confirm_btn.add_css_class("destructive-action")
    confirm_btn.set_sensitive(False)

    entry.connect("changed", lambda e: confirm_btn.set_sensitive(e.get_text().strip().upper() in ("EFFACER", "ERASE")))

    def _accept(*_args: Any) -> None:
        if entry.get_text().strip().upper() in ("EFFACER", "ERASE"):
            dialog.close()
            on_confirmed()

    confirm_btn.connect("clicked", _accept)
    entry.connect("entry-activated", _accept)

    btn_row.append(cancel_btn)
    btn_row.append(confirm_btn)
    content.append(btn_row)

    toolbar.set_content(content)
    dialog.set_child(toolbar)
    dialog.present(parent)


class UpdatesDialog(Adw.Dialog):
    """GitHub Releases cryptographic auto-updater dialog (GTK4/Libadwaita)."""

    def __init__(
        self,
        is_busy_fn: Callable[[], bool],
        on_toast: Callable[[str], None],
        initial_result: Optional[UpdateCheckResult] = None,
    ) -> None:
        super().__init__()
        self.set_title(t("update_title"))
        self.set_content_width(580)
        self.set_content_height(500)

        self._is_busy = is_busy_fn
        self._on_toast = on_toast
        self._result: Optional[UpdateCheckResult] = initial_result
        self._busy_net = False

        toolbar = Adw.ToolbarView()
        hdr = Adw.HeaderBar()
        self._btn_check = make_icon_button(t("update_btn_check"), "view-refresh-symbolic")
        self._btn_check.connect("clicked", lambda *_: self._start_check())
        hdr.pack_end(self._btn_check)
        toolbar.add_top_bar(hdr)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(14)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        grp = Adw.PreferencesGroup()
        self._row_ver = Adw.ActionRow(
            use_markup=False,
            title=t("update_current_ver", ver=get_current_version()),
            subtitle=t("update_status_idle"),
        )
        grp.add(self._row_ver)

        prefs = load_prefs()
        self._sw_auto = Adw.SwitchRow(use_markup=False, title=t("update_auto_chk"))
        self._sw_auto.set_active(bool(prefs.get("auto_check_updates", True)))
        self._sw_auto.connect("notify::active", lambda s, *_: save_prefs({"auto_check_updates": s.get_active()}))
        grp.add(self._sw_auto)
        box.append(grp)

        self._progress = Gtk.ProgressBar()
        self._progress.set_visible(False)
        box.append(self._progress)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_min_content_height(180)
        self._changelog_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self._changelog_view.add_css_class("bf-mono")
        self._changelog_buf = self._changelog_view.get_buffer()
        sw.set_child(self._changelog_view)
        box.append(sw)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.END)

        self._btn_skip = Gtk.Button(label=t("update_btn_skip"))
        self._btn_skip.set_visible(False)
        self._btn_skip.connect("clicked", self._on_skip_clicked)
        actions.append(self._btn_skip)

        self._btn_release_link = Gtk.Button(label=t("update_btn_open_release"))
        self._btn_release_link.set_visible(False)
        self._btn_release_link.connect("clicked", self._open_release_page)
        actions.append(self._btn_release_link)

        self._btn_install = Gtk.Button(label=t("update_btn_install"))
        self._btn_install.add_css_class("suggested-action")
        self._btn_install.set_sensitive(False)
        self._btn_install.connect("clicked", self._start_install)
        actions.append(self._btn_install)

        box.append(actions)
        toolbar.set_content(box)
        self.set_child(toolbar)

        if initial_result is not None:
            self._apply_result(initial_result)

    def _start_check(self) -> None:
        if self._busy_net:
            return
        self._busy_net = True
        self._btn_check.set_sensitive(False)
        self._btn_install.set_sensitive(False)
        self._row_ver.set_subtitle(t("update_status_checking"))

        def _worker() -> None:
            res = check_for_update()
            GLib.idle_add(self._on_check_done, res)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_check_done(self, res: UpdateCheckResult) -> None:
        self._busy_net = False
        self._btn_check.set_sensitive(True)
        self._apply_result(res)

    def _apply_result(self, res: UpdateCheckResult) -> None:
        self._result = res
        self._changelog_buf.set_text(res.changelog or "")
        self._btn_release_link.set_visible(bool(res.release_url))

        if res.status == "up_to_date":
            self._row_ver.set_subtitle(t("update_status_up_to_date", ver=res.current_version))
            self._btn_install.set_sensitive(False)
            self._btn_skip.set_visible(False)
        elif res.status == "available" and res.verified:
            self._row_ver.set_subtitle(t("update_status_available", ver=res.latest_version, asset=res.asset_name))
            self._btn_skip.set_visible(True)
            if self._is_busy():
                self._row_ver.set_subtitle(t("update_status_busy"))
                self._btn_install.set_sensitive(False)
            else:
                self._btn_install.set_sensitive(True)
        else:
            reason = res.error_message or res.status
            self._row_ver.set_subtitle(t("update_status_blocked", reason=reason))
            self._btn_install.set_sensitive(False)
            self._btn_skip.set_visible(False)

    def _on_skip_clicked(self, *_args: Any) -> None:
        if self._result and self._result.latest_version:
            save_prefs({"skipped_version": self._result.latest_version})
            self.close()

    def _open_release_page(self, *_args: Any) -> None:
        if self._result and self._result.release_url:
            Gio.AppInfo.launch_default_for_uri(self._result.release_url, None)

    def _start_install(self, *_args: Any) -> None:
        if not self._result or self._busy_net:
            return
        if self._is_busy():
            self._row_ver.set_subtitle(t("update_status_busy"))
            return
        res = self._result
        self._busy_net = True
        self._btn_install.set_sensitive(False)
        self._btn_check.set_sensitive(False)
        self._progress.set_visible(True)
        self._progress.set_fraction(0.0)
        self._row_ver.set_subtitle(t("update_status_downloading"))

        def _prog(received: int, total: int, _speed: float) -> None:
            frac = min(max(received / total, 0.0), 1.0) if total > 0 else 0.0
            GLib.idle_add(self._progress.set_fraction, frac)

        def _worker() -> None:
            try:
                asset_path = download_verified_update(res, progress_cb=_prog, is_busy_fn=self._is_busy)
                outcome = apply_downloaded_update(asset_path, res, is_busy_fn=self._is_busy)
                GLib.idle_add(self._on_install_done, True, outcome, "")
            except Exception as exc:
                GLib.idle_add(self._on_install_done, False, {}, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_install_done(self, ok: bool, outcome: Dict[str, Any], err: str) -> None:
        self._busy_net = False
        self._btn_check.set_sensitive(True)
        if not ok:
            self._row_ver.set_subtitle(t("update_status_blocked", reason=err))
            self._on_toast(err)
            return
        self._progress.set_fraction(1.0)
        act = outcome.get("action")
        if act == "installed_deb":
            msg = t("update_status_installed_deb")
        else:
            msg = t("update_status_extracted", path=outcome.get("path", ""))
        self._row_ver.set_subtitle(msg)
        self._on_toast(msg)
