"""Main BootForge native GTK4 / Libadwaita window & AdwApplication."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from engine.disk_utils import check_system_dependencies, list_block_devices
from engine.i18n import (
    get_current_language,
    set_current_language,
    subscribe_language_changed,
    t,
)
from bootforge_ui import __app_id__, __version__
from bootforge_ui.backend import ActiveProcessController, PROJECT_ROOT, run_async
from bootforge_ui.theme import format_bytes, format_speed, install_css, load_prefs, make_icon_button, save_prefs
from bootforge_ui.views_catalog import CatalogView
from bootforge_ui.views_direct import DirectFlashView
from bootforge_ui.views_toolbox import ToolboxView
from bootforge_ui.views_ventoy import MultiBootView
from bootforge_ui.widgets import PreflightDialog


class BootForgeWindow(Adw.ApplicationWindow):
    """Native GNOME/Libadwaita USB creator supporting Simple and Advanced modes."""

    def __init__(self, app: Adw.Application, initial_iso: str = "") -> None:
        super().__init__(application=app)
        self.set_title("BootForge")
        self.set_default_size(1160, 810)
        self.set_size_request(860, 620)

        self._proc = ActiveProcessController()
        self._devices: List[Dict[str, Any]] = []
        self._visible_devices: List[Dict[str, Any]] = []
        self._selected_device_path = ""
        self._show_locked = False
        self._deps: Optional[Dict[str, Any]] = None
        self._log_count = 0
        self._dropdown_updating = False
        self._mode_updating = False

        prefs = load_prefs()
        self._simple_mode = prefs.get("mode", "simple") != "advanced"

        self._style_mgr = Adw.StyleManager.get_default()
        self._style_mgr.set_color_scheme(Adw.ColorScheme.DEFAULT)

        self._register_window_actions()

        toolbar_view = Adw.ToolbarView()

        # ================= HEADER BAR =================
        header = Adw.HeaderBar()

        brand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_file = PROJECT_ROOT / "assets" / "icon.png"
        if icon_file.is_file():
            img = Gtk.Image.new_from_file(str(icon_file))
            img.set_pixel_size(22)
        else:
            img = Gtk.Image.new_from_icon_name("drive-removable-media-usb-symbolic")
            img.set_pixel_size(20)
        brand_box.append(img)

        brand_lbl = Gtk.Label(label="BootForge")
        brand_lbl.add_css_class("heading")
        brand_box.append(brand_lbl)
        header.pack_start(brand_box)

        self._switcher = Adw.ViewSwitcher()
        self._switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self._switcher)

        # Primary GNOME app menu
        self._app_menu = Gio.Menu()
        self._app_menu.append(t("menu_theme"), "win.toggle-theme")
        self._app_menu.append(t("menu_preflight"), "win.preflight")
        self._app_menu.append(t("menu_logs"), "win.toggle-logs")
        self._app_menu.append(t("menu_lang"), "win.toggle-lang")
        self._app_menu.append(t("menu_about"), "win.about")

        menu_btn = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            tooltip_text="Menu",
            menu_model=self._app_menu,
        )

        cur_lang = get_current_language()
        self._btn_lang = Gtk.Button(label="FR 🇫🇷" if cur_lang == "fr" else "EN 🇬🇧")
        self._btn_lang.set_tooltip_text(t("app_lang_tip"))
        self._btn_lang.connect("clicked", lambda *_: self._toggle_language())

        self._btn_console = Gtk.ToggleButton(
            icon_name="utilities-terminal-symbolic",
            tooltip_text=t("app_console_tip"),
        )
        self._btn_console.connect("toggled", self._on_console_toggled)

        # Linked Simple / Avancé mode selector
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        mode_box.add_css_class("linked")
        self._btn_simple = Gtk.ToggleButton(label=t("mode_simple"))
        self._btn_simple.set_tooltip_text(t("mode_simple_tip"))
        self._btn_advanced = Gtk.ToggleButton(label=t("mode_advanced"))
        self._btn_advanced.set_group(self._btn_simple)
        self._btn_advanced.set_tooltip_text(t("mode_advanced_tip"))

        self._btn_simple.connect("toggled", self._on_mode_toggled)
        mode_box.append(self._btn_simple)
        mode_box.append(self._btn_advanced)

        header.pack_end(menu_btn)
        header.pack_end(self._btn_lang)
        header.pack_end(self._btn_console)
        header.pack_end(mode_box)
        toolbar_view.add_top_bar(header)

        # ================= PERSISTENT TARGET USB BAR =================
        dev_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dev_bar.add_css_class("bf-device-bar")

        usb_ic = Gtk.Image.new_from_icon_name("drive-removable-media-usb-symbolic")
        usb_ic.set_pixel_size(18)
        dev_bar.append(usb_ic)

        self._usb_lbl = Gtk.Label(label="Clé USB :")
        self._usb_lbl.add_css_class("heading")
        dev_bar.append(self._usb_lbl)

        self._dev_dropdown = Gtk.DropDown()
        self._dev_dropdown.set_hexpand(True)
        self._dev_dropdown.connect("notify::selected", self._on_device_selected)
        dev_bar.append(self._dev_dropdown)

        self._dev_meta_lbl = Gtk.Label(label="")
        self._dev_meta_lbl.add_css_class("bf-status-pill")
        self._dev_meta_lbl.add_css_class("bf-pill-neutral")
        self._dev_meta_lbl.add_css_class("bf-mono")
        self._dev_meta_lbl.set_visible(False)
        dev_bar.append(self._dev_meta_lbl)

        self._chk_locked = Gtk.CheckButton(label="Disques système (0)")
        self._chk_locked.set_tooltip_text("Afficher les disques système verrouillés en lecture seule")
        self._chk_locked.connect("toggled", self._on_toggle_locked)
        dev_bar.append(self._chk_locked)

        self._btn_refresh_dev = make_icon_button("Actualiser", "view-refresh-symbolic")
        self._btn_refresh_dev.connect("clicked", lambda *_: self.refresh_devices())
        dev_bar.append(self._btn_refresh_dev)

        # ================= MAIN STACK + TOAST OVERLAY =================
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_vbox.append(dev_bar)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_vexpand(True)

        self._stack = Adw.ViewStack()
        self._switcher.set_stack(self._stack)

        self._view_direct = DirectFlashView(
            get_device=self.get_selected_device,
            is_busy=self._proc.is_busy,
            on_start_job=self.start_engine_job,
            on_log=self.append_log,
            on_toast=self.show_toast,
            on_layout_changed=lambda: None,
        )
        self._page_direct = self._stack.add_titled(self._view_direct, "direct", "Créer une clé")
        self._page_direct.set_icon_name("media-optical-symbolic")

        self._view_ventoy = MultiBootView(
            get_device=self.get_selected_device,
            is_busy=self._proc.is_busy,
            on_start_job=self.start_engine_job,
            on_log=self.append_log,
            on_toast=self.show_toast,
        )
        self._page_ventoy = self._stack.add_titled(self._view_ventoy, "ventoy", "Multi-systèmes")
        self._page_ventoy.set_icon_name("drive-multidisk-symbolic")

        self._view_toolbox = ToolboxView(
            get_device=self.get_selected_device,
            proc_ctrl=self._proc,
            on_start_job=self.start_engine_job,
            on_telemetry=self.handle_telemetry,
            on_log=self.append_log,
            on_toast=self.show_toast,
            on_busy_change=self._on_busy_changed,
        )
        self._page_toolbox = self._stack.add_titled(self._view_toolbox, "toolbox", "Réparer & Vérifier")
        self._page_toolbox.set_icon_name("applications-system-symbolic")

        self._view_catalog = CatalogView(
            proc_ctrl=self._proc,
            on_telemetry=self.handle_telemetry,
            on_log=self.append_log,
            on_toast=self.show_toast,
            on_busy_change=self._on_busy_changed,
            on_use_in_direct=self._jump_to_direct_with_iso,
            on_use_in_ventoy=self._jump_to_ventoy_with_iso,
        )
        self._page_catalog = self._stack.add_titled(self._view_catalog, "catalog", "Télécharger un OS")
        self._page_catalog.set_icon_name("folder-download-symbolic")

        self._toast_overlay.set_child(self._stack)
        main_vbox.append(self._toast_overlay)

        # ================= COLLAPSIBLE LOG CONSOLE =================
        self._console_revealer = Gtk.Revealer()
        self._console_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._console_revealer.set_reveal_child(False)

        term_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        term_wrap.add_css_class("bf-terminal-wrap")

        term_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        term_bar.set_margin_top(6)
        term_bar.set_margin_bottom(6)
        term_bar.set_margin_start(14)
        term_bar.set_margin_end(14)
        self._tlbl = Gtk.Label(label=t("bar_journal_title"))
        self._tlbl.add_css_class("caption")
        self._tlbl.add_css_class("bf-mono")
        self._tlbl.set_hexpand(True)
        self._tlbl.set_xalign(0)
        term_bar.append(self._tlbl)

        self._btn_copy_log = Gtk.Button(label=t("bar_journal_copy"))
        self._btn_copy_log.add_css_class("flat")
        self._btn_copy_log.connect("clicked", self._copy_logs)
        self._btn_clr_log = Gtk.Button(label=t("bar_journal_clear"))
        self._btn_clr_log.add_css_class("flat")
        self._btn_clr_log.connect("clicked", self._clear_logs)
        term_bar.append(self._btn_copy_log)
        term_bar.append(self._btn_clr_log)
        term_wrap.append(term_bar)

        term_scroll = Gtk.ScrolledWindow()
        term_scroll.set_min_content_height(150)
        term_scroll.set_max_content_height(190)
        self._log_view = Gtk.TextView(editable=False, cursor_visible=False)
        self._log_view.set_monospace(True)
        self._log_view.add_css_class("bf-terminal-view")
        self._log_view.set_top_margin(6)
        self._log_view.set_bottom_margin(8)
        self._log_view.set_left_margin(14)
        self._log_view.set_right_margin(14)
        term_scroll.set_child(self._log_view)
        term_wrap.append(term_scroll)

        self._console_revealer.set_child(term_wrap)
        main_vbox.append(self._console_revealer)

        # ================= BOTTOM STATUS & PROGRESS BAR =================
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        status_bar.add_css_class("bf-statusbar")

        self._status_badge = Gtk.Label(label=t("status_badge_ready"))
        self._status_badge.add_css_class("bf-status-pill")
        self._status_badge.add_css_class("bf-pill-ok")
        self._status_badge.add_css_class("bf-mono")
        status_bar.append(self._status_badge)

        self._status_msg = Gtk.Label(label=t("status_ready"), xalign=0)
        self._status_msg.set_ellipsize(Pango.EllipsizeMode.END)
        self._status_msg.set_hexpand(True)
        status_bar.append(self._status_msg)

        self._speed_lbl = Gtk.Label(label="")
        self._speed_lbl.add_css_class("caption")
        self._speed_lbl.add_css_class("bf-mono")
        self._speed_lbl.add_css_class("accent")
        self._speed_lbl.set_visible(False)
        status_bar.append(self._speed_lbl)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_size_request(190, -1)
        self._progress_bar.set_valign(Gtk.Align.CENTER)
        self._progress_bar.set_fraction(0.0)
        status_bar.append(self._progress_bar)

        self._pct_lbl = Gtk.Label(label="0 %")
        self._pct_lbl.add_css_class("caption")
        self._pct_lbl.add_css_class("bf-mono")
        status_bar.append(self._pct_lbl)

        self._btn_cancel = make_icon_button(t("bar_btn_cancel"), "process-stop-symbolic", ("destructive-action",))
        self._btn_cancel.set_visible(False)
        self._btn_cancel.connect("clicked", self._cancel_active)
        status_bar.append(self._btn_cancel)

        self._btn_log_count = Gtk.Button(label=t("bar_journal_btn", count=0))
        self._btn_log_count.add_css_class("flat")
        self._btn_log_count.add_css_class("bf-mono")
        self._btn_log_count.connect("clicked", lambda *_: self._btn_console.set_active(not self._btn_console.get_active()))
        status_bar.append(self._btn_log_count)

        main_vbox.append(status_bar)

        toolbar_view.set_content(main_vbox)
        self.set_content(toolbar_view)

        # Language listener
        subscribe_language_changed(self._on_language_changed)

        # Apply saved or default mode
        self.set_simple_mode(self._simple_mode, persist=False)

        # Initial load
        self.refresh_devices()
        self.refresh_deps()
        if initial_iso and os.path.isfile(initial_iso):
            self._view_direct.load_iso_external(initial_iso)

    def _register_window_actions(self) -> None:
        act_theme = Gio.SimpleAction.new("toggle-theme", None)
        act_theme.connect("activate", self._cycle_theme)
        self.add_action(act_theme)

        act_pre = Gio.SimpleAction.new("preflight", None)
        act_pre.connect("activate", self._open_preflight)
        self.add_action(act_pre)

        act_logs = Gio.SimpleAction.new("toggle-logs", None)
        act_logs.connect("activate", lambda *_: self._btn_console.set_active(not self._btn_console.get_active()))
        self.add_action(act_logs)

        act_lang = Gio.SimpleAction.new("toggle-lang", None)
        act_lang.connect("activate", lambda *_: self._toggle_language())
        self.add_action(act_lang)

        act_about = Gio.SimpleAction.new("about", None)
        act_about.connect("activate", self._open_about)
        self.add_action(act_about)

    def _on_language_changed(self, lang: str) -> None:
        GLib.idle_add(self.apply_language)

    def _toggle_language(self) -> None:
        new_lang = "en" if get_current_language() == "fr" else "fr"
        set_current_language(new_lang, persist=True)
        self.apply_language()

    def apply_language(self) -> None:
        lang = get_current_language()
        self._btn_lang.set_label("FR 🇫🇷" if lang == "fr" else "EN 🇬🇧")
        self._btn_lang.set_tooltip_text(t("app_lang_tip"))
        self._btn_console.set_tooltip_text(t("app_console_tip"))
        self._btn_refresh_dev.set_tooltip_text(t("app_refresh"))
        self._btn_cancel.set_tooltip_text(t("bar_btn_cancel"))
        self._tlbl.set_label(t("bar_journal_title"))
        self._btn_copy_log.set_label(t("bar_journal_copy"))
        self._btn_clr_log.set_label(t("bar_journal_clear"))
        self._btn_log_count.set_label(t("bar_journal_btn", count=self._log_count))
        if not self._proc.is_busy():
            self._status_badge.set_label(t("status_badge_ready"))
            self._status_msg.set_label(t("status_ready"))

        # Re-populate app menu with updated labels
        self._app_menu.remove_all()
        self._app_menu.append(t("menu_theme"), "win.toggle-theme")
        self._app_menu.append(t("menu_preflight"), "win.preflight")
        self._app_menu.append(t("menu_logs"), "win.toggle-logs")
        self._app_menu.append(t("menu_lang"), "win.toggle-lang")
        self._app_menu.append(t("menu_about"), "win.about")

        if hasattr(self._view_direct, "apply_language"):
            self._view_direct.apply_language()
        if hasattr(self._view_ventoy, "apply_language"):
            self._view_ventoy.apply_language()
        if hasattr(self._view_toolbox, "apply_language"):
            self._view_toolbox.apply_language()
        if hasattr(self._view_catalog, "apply_language"):
            self._view_catalog.apply_language()

        self.set_simple_mode(self._simple_mode, persist=False)

    def _on_mode_toggled(self, *_args: Any) -> None:
        if self._mode_updating:
            return
        self.set_simple_mode(self._btn_simple.get_active(), persist=True)

    def set_simple_mode(self, simple: bool, persist: bool = True) -> None:
        self._simple_mode = simple
        self._mode_updating = True
        try:
            self._btn_simple.set_active(simple)
            self._btn_advanced.set_active(not simple)
        finally:
            self._mode_updating = False

        if persist:
            save_prefs({"mode": "simple" if simple else "advanced"})

        if simple:
            self._page_direct.set_title(t("nav_direct_simple"))
            self._page_ventoy.set_title(t("nav_ventoy_simple"))
            self._page_toolbox.set_title(t("nav_toolbox_simple"))
            self._page_catalog.set_title(t("nav_catalog_simple"))

            self._usb_lbl.set_label(t("app_device_label"))
            self._chk_locked.set_visible(False)
            if self._show_locked:
                self._show_locked = False
                self._chk_locked.set_active(False)

            self._btn_console.set_visible(False)
            self._status_badge.set_visible(False)
            self._btn_log_count.set_visible(False)
            self._progress_bar.set_visible(self._proc.is_busy())
            self._pct_lbl.set_visible(self._proc.is_busy())
            if not self._proc.is_busy() and self._status_msg.get_label() in ("En attente d’instructions", "Waiting for instructions"):
                self._status_msg.set_label(t("status_ready"))
        else:
            self._page_direct.set_title(t("nav_direct_adv"))
            self._page_ventoy.set_title(t("nav_ventoy_adv"))
            self._page_toolbox.set_title(t("nav_toolbox_adv"))
            self._page_catalog.set_title(t("nav_catalog_adv"))

            self._usb_lbl.set_label(t("app_device_target"))
            self._chk_locked.set_visible(True)

            self._btn_console.set_visible(True)
            self._status_badge.set_visible(True)
            self._btn_log_count.set_visible(True)
            self._progress_bar.set_visible(True)
            self._pct_lbl.set_visible(True)

        self._btn_simple.set_label(t("mode_simple"))
        self._btn_simple.set_tooltip_text(t("mode_simple_tip"))
        self._btn_advanced.set_label(t("mode_advanced"))
        self._btn_advanced.set_tooltip_text(t("mode_advanced_tip"))
        self._chk_locked.set_tooltip_text(t("app_locked_tip"))

        self._view_direct.set_simple_mode(simple)
        self._view_ventoy.set_simple_mode(simple)
        self._view_toolbox.set_simple_mode(simple)
        self._view_catalog.set_simple_mode(simple)
        self._rebuild_device_dropdown()

    def _on_console_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._console_revealer.set_reveal_child(btn.get_active())

    def _cycle_theme(self, *_args: Any) -> None:
        if self._style_mgr.get_dark():
            self._style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            self._style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        if not self._simple_mode:
            self._view_direct.refresh_partition_map()
            self._view_ventoy.refresh_partition_map()

    def _open_about(self, *_args: Any) -> None:
        dlg = Adw.AboutDialog(
            application_name="BootForge",
            application_icon="drive-removable-media-usb-symbolic",
            version=__version__,
            developer_name="NiceMank",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/NiceMank/BootForge",
            issue_url="https://github.com/NiceMank/BootForge/issues",
            comments=t("dialog_about_desc"),
        )
        dlg.present(self)
        dlg.present(self)

    def refresh_devices(self) -> None:
        self._btn_refresh_dev.set_sensitive(False)

        def _ok(devs: List[Dict[str, Any]]) -> None:
            self._btn_refresh_dev.set_sensitive(True)
            self._devices = devs
            self._rebuild_device_dropdown()

        def _err(msg: str) -> None:
            self._btn_refresh_dev.set_sensitive(True)
            self.append_log("error", f"Erreur lecture disques : {msg}")

        run_async(list_block_devices, _ok, _err)

    def refresh_deps(self) -> None:
        def _ok(deps: Dict[str, Any]) -> None:
            self._deps = deps
            if not deps.get("core_ready"):
                self.show_toast("Certains outils Linux manquent : vérifiez le menu « Outils système installés ».")

        def _err(msg: str) -> None:
            self.append_log("error", f"Erreur vérification outils : {msg}")

        run_async(check_system_dependencies, _ok, _err)

    def _open_preflight(self, *_args: Any) -> None:
        PreflightDialog(self._deps, self.refresh_deps).present(self)

    def _on_toggle_locked(self, chk: Gtk.CheckButton) -> None:
        self._show_locked = chk.get_active()
        self._rebuild_device_dropdown()

    def _rebuild_device_dropdown(self) -> None:
        locked_count = sum(1 for d in self._devices if d.get("is_system_disk"))
        self._chk_locked.set_label(f"Disques système ({locked_count})")

        visible = (
            list(self._devices)
            if (self._show_locked and not self._simple_mode)
            else [d for d in self._devices if not d.get("is_system_disk")]
        )
        self._visible_devices = visible

        labels: List[str] = []
        if not visible:
            labels.append("Branchez une clé USB puis cliquez sur Actualiser")
        else:
            for d in visible:
                if self._simple_mode:
                    vtoy_tag = " (Multi-systèmes)" if d.get("ventoy_detected") else ""
                    labels.append(f"{d['model']} — {format_bytes(d['size_bytes'])}{vtoy_tag}")
                else:
                    lock_tag = " [SYSTÈME VERROUILLÉ]" if d.get("is_system_disk") else ""
                    vtoy_tag = " · Ventoy" if d.get("ventoy_detected") else ""
                    usb_tag = f" · USB {d['usb_speed_mbps']} Mb/s" if d.get("usb_speed_mbps") else ""
                    labels.append(
                        f"{d['model']} — {format_bytes(d['size_bytes'])} ({d['path']}){usb_tag}{vtoy_tag}{lock_tag}"
                    )

        self._dropdown_updating = True
        try:
            self._dev_dropdown.set_model(Gtk.StringList.new(labels))
            idx = 0
            if visible:
                match_idx = next((i for i, d in enumerate(visible) if d["path"] == self._selected_device_path), None)
                if match_idx is not None:
                    idx = match_idx
                else:
                    safe_idx = next((i for i, d in enumerate(visible) if not d.get("is_system_disk")), 0)
                    idx = safe_idx
                    self._selected_device_path = visible[idx]["path"]
            else:
                self._selected_device_path = ""
            self._dev_dropdown.set_selected(idx)
        finally:
            self._dropdown_updating = False

        self._sync_selected_device_ui()

    def _on_device_selected(self, dropdown: Gtk.DropDown, _pspec: Any) -> None:
        if self._dropdown_updating:
            return
        idx = dropdown.get_selected()
        if 0 <= idx < len(self._visible_devices):
            self._selected_device_path = self._visible_devices[idx]["path"]
        else:
            self._selected_device_path = ""
        self._sync_selected_device_ui()

    def get_selected_device(self) -> Optional[Dict[str, Any]]:
        if not self._selected_device_path:
            return None
        return next((d for d in self._devices if d["path"] == self._selected_device_path), None)

    def _sync_selected_device_ui(self) -> None:
        dev = self.get_selected_device()
        if not dev or self._simple_mode:
            self._dev_meta_lbl.set_visible(False)
        else:
            for cls in ("bf-pill-neutral", "bf-pill-err", "bf-pill-cyan"):
                self._dev_meta_lbl.remove_css_class(cls)
            if dev.get("is_system_disk"):
                self._dev_meta_lbl.set_label("DISQUE SYSTÈME")
                self._dev_meta_lbl.add_css_class("bf-pill-err")
            else:
                meta = []
                if dev.get("vid_pid"):
                    meta.append(dev["vid_pid"])
                if dev.get("pttype"):
                    meta.append(str(dev["pttype"]).upper())
                if dev.get("ventoy_detected"):
                    meta.append("VTOYEFI")
                self._dev_meta_lbl.set_label(" · ".join(meta) or "USB AMOVIBLE")
                self._dev_meta_lbl.add_css_class("bf-pill-cyan")
            self._dev_meta_lbl.set_visible(True)

        if not self._simple_mode:
            self._view_direct.refresh_partition_map()
        self._view_direct.sync_sensitivity()
        self._view_ventoy.on_device_changed()
        self._view_toolbox.sync_sensitivity()

    def _sync_all_views(self) -> None:
        self._view_direct.sync_sensitivity()
        self._view_ventoy.sync_sensitivity()
        self._view_toolbox.sync_sensitivity()
        self._view_catalog.sync_sensitivity()

    def _on_busy_changed(self, busy: bool) -> None:
        self._btn_cancel.set_visible(busy)
        if self._simple_mode:
            self._progress_bar.set_visible(busy)
            self._pct_lbl.set_visible(busy)
        self._sync_all_views()

    def start_engine_job(self, job: Dict[str, Any], dry_run: bool) -> None:
        if self._proc.is_busy():
            self.show_toast("Une opération est déjà en cours.")
            return

        self._progress_bar.set_visible(True)
        self._pct_lbl.set_visible(True)
        self._progress_bar.set_fraction(0.02)
        self._pct_lbl.set_label("2 %")
        self._set_status_badge("SIMULATION" if dry_run else "EN COURS", "bf-pill-warn")
        self._status_msg.set_label(
            "Vérification du plan..."
            if dry_run
            else ("Autorisation requise pour écrire sur la clé..." if self._simple_mode else f"Exécution ({job.get('action')})...")
        )
        self._btn_cancel.set_visible(True)
        self._sync_all_views()

        def _done(ok: bool, exit_code: int) -> None:
            self._btn_cancel.set_visible(False)
            self._speed_lbl.set_visible(False)
            if ok:
                self._progress_bar.set_fraction(1.0)
                self._pct_lbl.set_label("100 %")
                self._set_status_badge("TERMINÉ", "bf-pill-ok")
                self.show_toast("Simulation validée sans écriture." if dry_run else "Opération terminée avec succès !")
            else:
                self._set_status_badge("ÉCHEC", "bf-pill-err")
                if self._simple_mode:
                    self._progress_bar.set_visible(False)
                    self._pct_lbl.set_visible(False)
                self.show_toast(
                    "L’opération a été annulée ou a échoué."
                    if self._simple_mode
                    else f"Opération arrêtée ou en erreur (code {exit_code})."
                )
            self.refresh_devices()
            self._sync_all_views()

        self._proc.run_engine_job(job, dry_run, self.handle_telemetry, _done)

    def _cancel_active(self, *_args: Any) -> None:
        self._proc.cancel()
        self.append_log("warn", "Annulation demandée par l’utilisateur.")
        self._set_status_badge("ANNULÉ", "bf-pill-warn")
        self._status_msg.set_label("Opération annulée.")

    def _set_status_badge(self, text: str, cls: str) -> None:
        self._status_badge.set_label(text)
        for c in ("bf-pill-ok", "bf-pill-warn", "bf-pill-err", "bf-pill-cyan"):
            self._status_badge.remove_css_class(c)
        self._status_badge.add_css_class(cls)

    def handle_telemetry(self, ev: Dict[str, Any]) -> None:
        etype = ev.get("type")
        if etype == "progress":
            self._progress_bar.set_visible(True)
            self._pct_lbl.set_visible(True)
            pct = float(ev.get("percent") or 0.0)
            self._progress_bar.set_fraction(max(0.0, min(1.0, pct / 100.0)))
            self._pct_lbl.set_label(f"{pct:.0f} %" if self._simple_mode else f"{pct:.1f}%")
            if ev.get("message"):
                self._status_msg.set_label(str(ev["message"]))
            spd = ev.get("speed_bps")
            if spd and not self._simple_mode:
                self._speed_lbl.set_label(format_speed(float(spd)))
                self._speed_lbl.set_visible(True)
            else:
                self._speed_lbl.set_visible(False)
            stage = str(ev.get("stage") or "")
            if stage == "done":
                self._set_status_badge("TERMINÉ", "bf-pill-ok")
            else:
                self._set_status_badge(stage.upper()[:10] or "ACTIF", "bf-pill-cyan")
        elif etype == "log":
            self.append_log(str(ev.get("level") or "info"), str(ev.get("message") or ""))
        elif etype == "error":
            msg = str(ev.get("message") or "Erreur moteur")
            self.append_log("error", msg)
            self._status_msg.set_label(msg)
            self._set_status_badge("ERREUR", "bf-pill-err")
            self.show_toast(msg)
        elif etype == "benchmark_result":
            mbps = float(ev.get("read_mbps") or 0.0)
            self._view_toolbox.set_benchmark_result(mbps)
            self.append_log("info", f"Vitesse de lecture USB : {mbps:.2f} Mo/s")
        elif etype == "dry_run_plan":
            self.append_log("info", f"Simulation validée : {ev.get('mode')} sur {ev.get('device')}")

    def append_log(self, level: str, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{level.upper():7s}] {message}\n"
        buf = self._log_view.get_buffer()
        end_iter = buf.get_end_iter()
        buf.insert(end_iter, line)
        self._log_count += 1
        self._btn_log_count.set_label(f"Journal ({self._log_count})")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self._log_view.scroll_mark_onscreen(mark)
        buf.delete_mark(mark)

    def _copy_logs(self, *_args: Any) -> None:
        buf = self._log_view.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if txt:
            disp = Gdk.Display.get_default()
            if disp:
                disp.get_clipboard().set(txt)
            self.show_toast("Journal copié.")

    def _clear_logs(self, *_args: Any) -> None:
        self._log_view.get_buffer().set_text("")
        self._log_count = 0
        self._btn_log_count.set_label("Journal (0)")

    def show_toast(self, message: str) -> None:
        toast = Adw.Toast(title=message, timeout=4)
        self._toast_overlay.add_toast(toast)

    def _jump_to_direct_with_iso(self, path: str) -> None:
        self._view_direct.load_iso_external(path)
        self._stack.set_visible_child_name("direct")

    def _jump_to_ventoy_with_iso(self, path: str) -> None:
        self._view_ventoy.add_iso_external(path)
        self._stack.set_visible_child_name("ventoy")


class BootForgeApplication(Adw.Application):
    """Main Libadwaita application supporting direct launch and opening .iso/.img arguments."""

    def __init__(self) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        GLib.set_prgname("bootforge")
        GLib.set_application_name("BootForge")
        self._initial_iso = ""

    def do_activate(self) -> None:
        install_css()
        Gtk.Window.set_default_icon_name("bootforge")
        win = self.get_active_window()
        if not win:
            win = BootForgeWindow(self, initial_iso=self._initial_iso)
        win.present()

    def do_open(self, files: List[Gio.File], _n_files: int, _hint: str) -> None:
        if files and files[0].get_path():
            self._initial_iso = files[0].get_path() or ""
        self.do_activate()
        win = self.get_active_window()
        if isinstance(win, BootForgeWindow) and self._initial_iso:
            win._jump_to_direct_with_iso(self._initial_iso)


def main(argv: Optional[List[str]] = None) -> int:
    app = BootForgeApplication()
    return app.run(argv if argv is not None else sys.argv)
