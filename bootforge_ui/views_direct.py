"""Direct USB creation (Rufus-compatible) native GTK4/Libadwaita view."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, Gio, Gtk

from engine.disk_utils import plan_partition_layout, sanitize_volume_label
from engine.i18n import t
from engine.iso_inspector import inspect_image
from bootforge_ui.backend import run_async
from bootforge_ui.theme import esc, format_bytes, make_icon_button, set_button_text
from bootforge_ui.widgets import PartitionMapWidget, WinTweaksDialog, present_confirm_destroy_dialog


class DirectFlashView(Gtk.ScrolledWindow):
    """Single-system USB creation view adapting between Simple and Advanced modes."""

    def __init__(
        self,
        get_device: Callable[[], Optional[Dict[str, Any]]],
        is_busy: Callable[[], bool],
        on_start_job: Callable[[Dict[str, Any], bool], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        on_layout_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._get_device = get_device
        self._is_busy = is_busy
        self._on_start_job = on_start_job
        self._on_log = on_log
        self._on_toast = on_toast
        self._on_layout_changed = on_layout_changed

        self._simple_mode = True
        self._iso_path = ""
        self._iso_info: Optional[Dict[str, Any]] = None
        self._driver_dir = ""
        self._syncing_controls = False

        self._win_tweaks: Dict[str, Any] = {
            "enabled": True,
            "bypass_tpm_secureboot_ram": True,
            "bypass_microsoft_account": True,
            "disable_bitlocker": True,
            "disable_telemetry": True,
            "disable_consumer_bloat": False,
            "skip_product_key": True,
            "create_local_account": False,
            "local_username": "Utilisateur",
            "local_password": "",
            "locale": "fr-FR",
            "timezone": "Romance Standard Time",
        }

        self._clamp = Adw.Clamp(
            maximum_size=760,
            margin_top=14,
            margin_bottom=18,
            margin_start=16,
            margin_end=16,
        )
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        self._cols = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._cols.set_homogeneous(False)

        # ================= COLUMN A: Image Source & Drivers =================
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        self._grp_iso = Adw.PreferencesGroup()

        drop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        drop_box.add_css_class("bf-dropzone")
        drop_box.set_margin_bottom(8)
        drop_box.set_margin_start(2)
        drop_box.set_margin_end(2)

        drop_pad = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        drop_pad.set_margin_top(10)
        drop_pad.set_margin_bottom(10)
        drop_pad.set_margin_start(12)
        drop_pad.set_margin_end(12)
        drop_pad.set_hexpand(True)

        ic = Gtk.Image.new_from_icon_name("media-optical-symbolic")
        ic.set_pixel_size(24)
        ic.add_css_class("accent")
        drop_pad.append(ic)

        self._drop_lbl = Gtk.Label(
            label="Glissez un fichier .iso ou .img ici, ou cliquez sur Choisir",
            xalign=0,
            wrap=True,
        )
        self._drop_lbl.set_hexpand(True)
        drop_pad.append(self._drop_lbl)

        self._pick_iso_btn = make_icon_button("Choisir...", "document-open-symbolic", ("suggested-action",))
        self._pick_iso_btn.connect("clicked", self._pick_iso_file)
        drop_pad.append(self._pick_iso_btn)
        drop_box.append(drop_pad)

        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("enter", lambda *_: (drop_box.add_css_class("bf-drag-hover"), Gdk.DragAction.COPY)[1])
        drop_target.connect("leave", lambda *_: drop_box.remove_css_class("bf-drag-hover"))
        drop_target.connect("drop", self._on_file_drop)
        drop_box.add_controller(drop_target)
        self._grp_iso.add(drop_box)

        self._iso_entry = Adw.EntryRow(use_markup=False, title="Chemin complet du fichier")
        self._analyze_btn = Gtk.Button(label="Analyser")
        self._analyze_btn.set_valign(Gtk.Align.CENTER)
        self._analyze_btn.connect("clicked", lambda *_: self.inspect_iso_path(self._iso_entry.get_text().strip()))
        self._iso_entry.add_suffix(self._analyze_btn)
        self._iso_entry.connect("entry-activated", lambda *_: self.inspect_iso_path(self._iso_entry.get_text().strip()))
        self._iso_entry.connect("changed", self._on_iso_text_changed)
        self._grp_iso.add(self._iso_entry)

        self._row_detected = Adw.ActionRow(
            use_markup=False,
            title="Fichier sélectionné",
            subtitle="Aucun fichier choisi pour l’instant",
        )
        self._row_detected.set_icon_name("computer-symbolic")
        self._badge_os = Gtk.Label(label="En attente")
        self._badge_os.add_css_class("bf-status-pill")
        self._badge_os.add_css_class("bf-pill-neutral")
        self._badge_os.set_valign(Gtk.Align.CENTER)
        self._row_detected.add_suffix(self._badge_os)
        self._grp_iso.add(self._row_detected)

        self._row_boot_note = Adw.ActionRow(
            use_markup=False,
            title="Méthode d’amorçage détectée",
            subtitle="UEFI:NTFS, découpe FAT32 ou mode DD sélectionné automatiquement.",
        )
        self._row_boot_note.set_icon_name("emblem-system-symbolic")
        self._grp_iso.add(self._row_boot_note)
        left_col.append(self._grp_iso)

        # Advanced-only driver injection ($WinPEDriver$)
        self._grp_drv = Adw.PreferencesGroup(
            title=esc("Pilotes Windows supplémentaires ($WinPEDriver$)"),
            description=esc("Optionnel : ajoutez vos pilotes NVMe/RST ou Wi-Fi pour Windows Setup."),
        )
        self._drv_row = Adw.ActionRow(
            use_markup=False,
            title="Dossier de pilotes (.inf)",
            subtitle="Aucun dossier sélectionné",
        )
        self._drv_row.set_icon_name("folder-open-symbolic")
        self._drv_clear_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Effacer")
        self._drv_clear_btn.add_css_class("flat")
        self._drv_clear_btn.set_valign(Gtk.Align.CENTER)
        self._drv_clear_btn.set_visible(False)
        self._drv_clear_btn.connect("clicked", lambda *_: self._set_driver_dir(""))

        drv_pick_btn = Gtk.Button(label="Choisir...")
        drv_pick_btn.set_valign(Gtk.Align.CENTER)
        drv_pick_btn.connect("clicked", self._pick_driver_folder)
        self._drv_row.add_suffix(self._drv_clear_btn)
        self._drv_row.add_suffix(drv_pick_btn)
        self._grp_drv.add(self._drv_row)
        left_col.append(self._grp_drv)

        self._cols.append(left_col)

        # ================= COLUMN B: Options & Partitioning =================
        right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        self._grp_fmt = Adw.PreferencesGroup()

        self._write_modes = ["iso", "dd"]
        self._combo_write = Adw.ComboRow(use_markup=False, title="Mode d’écriture")
        self._combo_write.set_model(
            Gtk.StringList.new(["ISO (Extraction)", "DD (Image brute)"])
        )
        self._combo_write.set_selected(0)
        self._combo_write.connect("notify::selected", lambda *_: self._sync_ui_state())
        self._grp_fmt.add(self._combo_write)

        self._schemes = ["gpt", "mbr"]
        self._combo_scheme = Adw.ComboRow(use_markup=False, title="Schéma de partition")
        self._combo_scheme.set_model(
            Gtk.StringList.new(["GPT (UEFI moderne)", "MBR (BIOS / UEFI)"])
        )
        self._combo_scheme.set_selected(0)
        self._combo_scheme.connect("notify::selected", lambda *_: self._emit_layout_change())
        self._grp_fmt.add(self._combo_scheme)

        self._filesystems = ["ntfs", "fat32", "exfat"]
        self._combo_fs = Adw.ComboRow(use_markup=False, title="Système de fichiers")
        self._combo_fs.set_model(
            Gtk.StringList.new(
                [
                    "NTFS + UEFI:NTFS",
                    "FAT32 (WIM split)",
                    "exFAT (+ UEFI:NTFS)",
                ]
            )
        )
        self._combo_fs.set_selected(0)
        self._combo_fs.connect("notify::selected", lambda *_: self._sync_ui_state())
        self._grp_fmt.add(self._combo_fs)

        self._entry_label = Adw.EntryRow(use_markup=False, title="Nom de la clé")
        self._entry_label.set_text("BOOTFORGE")
        self._entry_label.connect("changed", lambda *_: self._update_label_badge())
        self._lbl_sanitized = Gtk.Label(label="BOOTFORGE")
        self._lbl_sanitized.add_css_class("caption")
        self._lbl_sanitized.add_css_class("bf-mono")
        self._lbl_sanitized.add_css_class("dim-label")
        self._lbl_sanitized.set_valign(Gtk.Align.CENTER)
        self._entry_label.add_suffix(self._lbl_sanitized)
        self._grp_fmt.add(self._entry_label)

        self._sw_quick = Adw.SwitchRow(
            use_markup=False,
            title="Formatage rapide",
            subtitle="Recommandé pour préparer la clé rapidement",
        )
        self._sw_quick.set_active(True)
        self._grp_fmt.add(self._sw_quick)
        right_col.append(self._grp_fmt)

        # Conditional Windows group (adapts between 1 simple toggle vs XML button)
        self._grp_win = Adw.PreferencesGroup()
        self._sw_win_simple = Adw.SwitchRow(
            use_markup=False,
            title="Installer Windows 11 sans blocage",
            subtitle="Désactive la vérification TPM 2.0, Secure Boot et évite le compte Microsoft obligatoire",
        )
        self._sw_win_simple.set_active(True)
        self._sw_win_simple.connect("notify::active", self._on_simple_win_toggled)
        self._grp_win.add(self._sw_win_simple)

        self._row_win_tweaks = Adw.ActionRow(
            use_markup=False,
            title="Options Windows (XML)",
            subtitle="Contournement TPM, compte local, BitLocker, télémétrie",
        )
        self._row_win_tweaks.set_icon_name("security-high-symbolic")
        self._badge_win_count = Gtk.Label(label="6 actives")
        self._badge_win_count.add_css_class("bf-status-pill")
        self._badge_win_count.add_css_class("bf-pill-warn")
        self._badge_win_count.set_valign(Gtk.Align.CENTER)
        btn_tweaks = Gtk.Button(label="Configurer...")
        btn_tweaks.set_valign(Gtk.Align.CENTER)
        btn_tweaks.connect("clicked", self._open_win_tweaks)
        self._row_win_tweaks.add_suffix(self._badge_win_count)
        self._row_win_tweaks.add_suffix(btn_tweaks)
        self._grp_win.add(self._row_win_tweaks)

        self._win_tip_row = Adw.ActionRow(
            use_markup=False,
            title="Conseil lors de l’installation",
            subtitle="Débranchez si possible les autres disques pendant l’installation Windows.",
        )
        self._win_tip_row.set_icon_name("dialog-information-symbolic")
        self._grp_win.add(self._win_tip_row)
        right_col.append(self._grp_win)

        # Conditional Linux Persistence group
        self._grp_persist = Adw.PreferencesGroup()
        self._spin_persist = Adw.SpinRow.new_with_range(0, 64, 1)
        self._spin_persist.set_use_markup(False)
        self._spin_persist.set_value(0)
        self._spin_persist.connect("notify::value", lambda *_: self._emit_layout_change())
        self._grp_persist.add(self._spin_persist)

        self._persist_types = ["casper-rw", "persistence"]
        self._combo_persist_type = Adw.ComboRow(use_markup=False, title="Format de persistance")
        self._combo_persist_type.set_model(
            Gtk.StringList.new(["casper-rw (Ubuntu/Mint)", "persistence (Kali/Debian)"])
        )
        self._combo_persist_type.set_selected(0)
        self._grp_persist.add(self._combo_persist_type)
        right_col.append(self._grp_persist)

        self._cols.append(right_col)
        root_box.append(self._cols)

        # Advanced-only PartitionMap visualizer
        self._part_frame = Adw.PreferencesGroup()
        self._part_map = PartitionMapWidget()
        map_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        map_holder.set_margin_top(10)
        map_holder.set_margin_bottom(10)
        map_holder.set_margin_start(12)
        map_holder.set_margin_end(12)
        map_holder.append(self._part_map)
        self._part_frame.add(map_holder)
        root_box.append(self._part_frame)

        # Action footer
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._action_hint = Gtk.Label(xalign=0, wrap=True)
        self._action_hint.add_css_class("caption")
        self._action_hint.add_css_class("dim-label")
        self._action_hint.set_hexpand(True)
        action_box.append(self._action_hint)

        self._btn_dry = make_icon_button("Simuler", "system-run-symbolic")
        self._btn_dry.connect("clicked", lambda *_: self._trigger_flash(dry_run=True))

        self._btn_flash = make_icon_button("Créer la clé", "media-playback-start-symbolic", ("suggested-action",))
        self._btn_flash.connect("clicked", lambda *_: self._trigger_flash(dry_run=False))

        action_box.append(self._btn_dry)
        action_box.append(self._btn_flash)
        root_box.append(action_box)

        self._clamp.set_child(root_box)
        self.set_child(self._clamp)
        self.set_simple_mode(True)

    def apply_language(self) -> None:
        """Update localized strings."""
        set_button_text(self._pick_iso_btn, t("catalog_btn_browse"))
        set_button_text(self._btn_dry, t("direct_btn_dry"))
        self._analyze_btn.set_label(t("direct_btn_analyze"))
        self._sw_win_simple.set_title(t("direct_s_win_tweaks_row"))
        self._sw_win_simple.set_subtitle(t("direct_s_win_tweaks_sub"))
        self._row_win_tweaks.set_title(t("direct_a_win_tweaks_row"))
        self.set_simple_mode(self._simple_mode)

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple
        if simple:
            self._clamp.set_maximum_size(760)
            self._cols.set_orientation(Gtk.Orientation.VERTICAL)
            self._cols.set_homogeneous(False)

            self._grp_iso.set_title(esc(t("direct_s_step1_title")))
            self._grp_iso.set_description(esc(t("direct_s_step1_desc")))
            self._drop_lbl.set_label(t("direct_s_drop_label"))

            self._grp_fmt.set_title(esc(t("direct_s_step2_title")))
            self._grp_fmt.set_description(esc(t("direct_s_step2_desc")))
            self._entry_label.set_title(t("direct_s_label_title"))

            self._grp_win.set_title(esc(t("direct_s_win_title")))
            self._grp_persist.set_title(esc(t("direct_s_persist_title")))
            self._grp_persist.set_description(esc(t("direct_s_persist_desc")))
            self._spin_persist.set_title(t("direct_s_persist_size"))
            self._spin_persist.set_subtitle(t("direct_s_persist_sub"))

            set_button_text(self._btn_flash, t("direct_s_btn_flash"))
        else:
            self._clamp.set_maximum_size(1180)
            self._cols.set_orientation(Gtk.Orientation.HORIZONTAL)
            self._cols.set_homogeneous(True)

            self._grp_iso.set_title(esc(t("direct_a_step1_title")))
            self._grp_iso.set_description(esc(t("direct_a_step1_desc")))
            self._drop_lbl.set_label(t("direct_a_drop_label"))

            self._grp_fmt.set_title(esc(t("direct_a_step2_title")))
            self._grp_fmt.set_description(esc(t("direct_a_step2_desc")))
            self._entry_label.set_title(t("direct_a_label_title"))

            self._grp_win.set_title(esc(t("direct_a_win_title")))
            self._grp_persist.set_title(esc(t("direct_a_persist_title")))
            self._grp_persist.set_description(esc(t("direct_a_persist_desc")))
            self._spin_persist.set_title(t("direct_a_persist_size"))
            self._spin_persist.set_subtitle(t("direct_a_persist_sub"))

            set_button_text(self._btn_flash, t("direct_a_btn_flash"))

        # Toggle expert-only rows cleanly
        self._iso_entry.set_visible(not simple)
        self._badge_os.set_visible(not simple)
        self._row_boot_note.set_visible(not simple)
        self._grp_drv.set_visible(not simple)

        self._combo_write.set_visible(not simple)
        self._combo_scheme.set_visible(not simple)
        self._combo_fs.set_visible(not simple)
        self._lbl_sanitized.set_visible(not simple)
        self._sw_quick.set_visible(not simple)

        self._sw_win_simple.set_visible(simple)
        self._row_win_tweaks.set_visible(not simple)
        self._combo_persist_type.set_visible(not simple)
        self._part_frame.set_visible(not simple)
        self._btn_dry.set_visible(not simple)

        self._refresh_detected_summary()
        self._sync_ui_state()

    def _on_simple_win_toggled(self, sw: Adw.SwitchRow, _pspec: Any) -> None:
        if self._syncing_controls:
            return
        active = sw.get_active()
        self._win_tweaks["enabled"] = active
        if active:
            self._win_tweaks["bypass_tpm_secureboot_ram"] = True
            self._win_tweaks["bypass_microsoft_account"] = True
            self._win_tweaks["disable_bitlocker"] = True
            self._win_tweaks["skip_product_key"] = True
        self._update_win_badge()

    def load_iso_external(self, path: str) -> None:
        self._iso_entry.set_text(path)
        self.inspect_iso_path(path)

    def _on_iso_text_changed(self, entry: Adw.EntryRow) -> None:
        self._iso_path = entry.get_text().strip()
        self._sync_action_sensitivity()

    def _on_file_drop(self, _target: Gtk.DropTarget, value: Any, _x: float, _y: float) -> bool:
        if isinstance(value, Gio.File):
            fpath = value.get_path()
            if fpath:
                self.load_iso_external(fpath)
                return True
        return False

    def _pick_iso_file(self, *_args: Any) -> None:
        dlg = Gtk.FileDialog(title="Choisir un fichier système (.iso ou .img)")
        ff = Gtk.FileFilter()
        ff.set_name("Images bootables (ISO, IMG, RAW, VHD)")
        for pat in ("*.iso", "*.ISO", "*.img", "*.IMG", "*.raw", "*.vhd", "*.vhdx", "*.gz", "*.xz"):
            ff.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dlg.set_filters(filters)

        win = self.get_root()
        assert isinstance(win, Gtk.Window)

        def _done(source: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = source.open_finish(res)
                if f and f.get_path():
                    self.load_iso_external(f.get_path() or "")
            except Exception:
                pass

        dlg.open(win, None, _done)

    def _set_driver_dir(self, path: str) -> None:
        self._driver_dir = path
        self._drv_row.set_subtitle(path if path else "Aucun dossier sélectionné")
        self._drv_clear_btn.set_visible(bool(path))

    def _pick_driver_folder(self, *_args: Any) -> None:
        dlg = Gtk.FileDialog(title="Choisir le dossier de pilotes ($WinPEDriver$)")
        win = self.get_root()
        assert isinstance(win, Gtk.Window)

        def _done(source: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = source.select_folder_finish(res)
                if f and f.get_path():
                    self._set_driver_dir(f.get_path() or "")
            except Exception:
                pass

        dlg.select_folder(win, None, _done)

    def inspect_iso_path(self, raw_path: str) -> None:
        path = raw_path.strip()
        if not path:
            return
        self._iso_path = path
        self._analyze_btn.set_sensitive(False)
        self._row_detected.set_subtitle("Analyse du fichier en cours...")
        self._on_log("info", f"Analyse de l’image : {path}")

        def _do() -> Dict[str, Any]:
            return inspect_image(path)

        def _ok(info: Dict[str, Any]) -> None:
            self._analyze_btn.set_sensitive(True)
            self._iso_info = info
            self._apply_iso_recommendations(info)
            self._on_log(
                "info",
                f"Image analysée : {info['filename']} ({info['os_label']}, {format_bytes(info['size_bytes'])})",
            )

        def _err(msg: str) -> None:
            self._analyze_btn.set_sensitive(True)
            self._iso_info = None
            self._row_detected.set_subtitle(f"Impossible de lire ce fichier : {msg}")
            self._badge_os.set_label("Erreur")
            self._on_toast(msg)
            self._sync_ui_state()

        run_async(_do, _ok, _err)

    def _refresh_detected_summary(self) -> None:
        info = self._iso_info
        if not info:
            self._row_detected.set_title("Fichier sélectionné" if self._simple_mode else "Système détecté")
            self._row_detected.set_subtitle(
                "Aucun fichier choisi pour l’instant" if self._simple_mode else "Aucune image analysée"
            )
            return

        if self._simple_mode:
            os_lbl = str(info.get("os_label") or "Système prêt à installer")
            self._row_detected.set_title(f"{info.get('filename', '')}")
            self._row_detected.set_subtitle(f"{os_lbl} · {format_bytes(info.get('size_bytes', 0))}")
        else:
            self._row_detected.set_title("Système détecté")
            self._row_detected.set_subtitle(
                f"{info.get('filename', '')} · {format_bytes(info.get('size_bytes', 0))} · {info.get('architecture', '')}"
            )

    def _apply_iso_recommendations(self, info: Dict[str, Any]) -> None:
        self._syncing_controls = True
        try:
            rec_write = info.get("recommended_write_mode", "iso")
            self._combo_write.set_selected(self._write_modes.index(rec_write) if rec_write in self._write_modes else 0)

            rec_scheme = info.get("recommended_scheme", "gpt")
            self._combo_scheme.set_selected(self._schemes.index(rec_scheme) if rec_scheme in self._schemes else 0)

            rec_fs = info.get("recommended_fs", "fat32")
            self._combo_fs.set_selected(self._filesystems.index(rec_fs) if rec_fs in self._filesystems else 0)

            os_fam = info.get("os_family", "unknown")
            if os_fam == "windows":
                self._entry_label.set_text("WIN11_USB")
            elif os_fam == "linux":
                self._entry_label.set_text("LINUX_LIVE")
                ptype = info.get("persistence_type", "none")
                if ptype == "debian-persistence":
                    self._combo_persist_type.set_selected(1)
                elif ptype == "casper-rw":
                    self._combo_persist_type.set_selected(0)
            else:
                self._entry_label.set_text("BOOTFORGE")

            self._badge_os.set_label(str(info.get("os_label") or os_fam).upper())
            for cls in ("bf-pill-neutral", "bf-pill-warn", "bf-pill-cyan", "bf-pill-ok"):
                self._badge_os.remove_css_class(cls)
            self._badge_os.add_css_class("bf-pill-warn" if os_fam == "windows" else "bf-pill-cyan")

            notes = info.get("notes") or []
            wim_sz = int(info.get("install_wim_size") or 0)
            if wim_sz > 0:
                self._row_boot_note.set_subtitle(
                    f"install.wim : {format_bytes(wim_sz)}" + (f" — {notes[0]}" if notes else "")
                )
            elif notes:
                self._row_boot_note.set_subtitle(" · ".join(notes[:2]))
            else:
                self._row_boot_note.set_subtitle(
                    f"Amorce UEFI : {'Oui' if info.get('has_uefi_boot') else 'Hybride/ISO'}"
                )
        finally:
            self._syncing_controls = False

        self._refresh_detected_summary()
        self._sync_ui_state()

    def _current_write_mode(self) -> str:
        idx = self._combo_write.get_selected()
        return self._write_modes[idx] if 0 <= idx < len(self._write_modes) else "iso"

    def _current_scheme(self) -> str:
        idx = self._combo_scheme.get_selected()
        return self._schemes[idx] if 0 <= idx < len(self._schemes) else "gpt"

    def _current_fs(self) -> str:
        idx = self._combo_fs.get_selected()
        return self._filesystems[idx] if 0 <= idx < len(self._filesystems) else "ntfs"

    def _update_label_badge(self) -> None:
        sanitized = sanitize_volume_label(self._entry_label.get_text(), self._current_fs())
        self._lbl_sanitized.set_label(sanitized)

    def _sync_ui_state(self) -> None:
        is_iso_mode = self._current_write_mode() == "iso"
        self._combo_scheme.set_sensitive(is_iso_mode)
        self._combo_fs.set_sensitive(is_iso_mode)
        self._entry_label.set_sensitive(is_iso_mode)
        self._sw_quick.set_sensitive(is_iso_mode)

        os_fam = (self._iso_info or {}).get("os_family", "unknown")
        if self._simple_mode:
            # Show Windows options only when Windows image is detected; Linux persistence only for Linux
            self._grp_win.set_visible(is_iso_mode and os_fam == "windows")
            self._win_tip_row.set_visible(is_iso_mode and os_fam == "windows")
            self._grp_persist.set_visible(is_iso_mode and os_fam == "linux")
        else:
            self._grp_win.set_visible(is_iso_mode and os_fam != "linux")
            self._win_tip_row.set_visible(is_iso_mode and os_fam == "windows")
            self._grp_persist.set_visible(is_iso_mode and os_fam != "windows")

        self._syncing_controls = True
        try:
            self._sw_win_simple.set_active(bool(self._win_tweaks.get("enabled", True)))
        finally:
            self._syncing_controls = False

        self._update_win_badge()
        self._update_label_badge()
        if not self._simple_mode:
            self.refresh_partition_map()
        self._sync_action_sensitivity()
        if not self._syncing_controls:
            self._on_layout_changed()

    def _emit_layout_change(self) -> None:
        if not self._simple_mode:
            self.refresh_partition_map()
        if not self._syncing_controls:
            self._on_layout_changed()

    def refresh_partition_map(self) -> None:
        dev = self._get_device()
        disk_bytes = int(dev.get("size_bytes", 32 * 1024**3)) if dev else 32 * 1024**3
        write_mode = self._current_write_mode()
        scheme = self._current_scheme()
        fs = self._current_fs()
        os_fam = (self._iso_info or {}).get("os_family", "unknown")
        persist_gb = float(self._spin_persist.get_value()) if (write_mode == "iso" and os_fam != "windows") else 0.0

        segs = plan_partition_layout(
            disk_bytes,
            "direct",
            scheme=scheme,
            filesystem=fs,
            write_mode=write_mode,
            persistence_gb=persist_gb,
            uefi_ntfs=(fs in ("ntfs", "exfat")),
        )
        badge = "DD RAW" if write_mode == "dd" else f"{scheme.upper()} · UEFI"
        self._part_map.set_layout(segs, title="Partitions prévues sur la clé USB", badge=badge)

    def _update_win_badge(self) -> None:
        if not self._win_tweaks.get("enabled", True):
            self._badge_win_count.set_label("Désactivé")
            return
        keys = (
            "bypass_tpm_secureboot_ram",
            "bypass_microsoft_account",
            "disable_bitlocker",
            "disable_telemetry",
            "disable_consumer_bloat",
            "skip_product_key",
            "create_local_account",
        )
        cnt = sum(1 for k in keys if self._win_tweaks.get(k))
        self._badge_win_count.set_label(f"{cnt} active(s)")

    def _open_win_tweaks(self, *_args: Any) -> None:
        def _applied(opts: Dict[str, Any]) -> None:
            self._win_tweaks = opts
            self._syncing_controls = True
            try:
                self._sw_win_simple.set_active(bool(opts.get("enabled", True)))
            finally:
                self._syncing_controls = False
            self._update_win_badge()
            self._on_toast("Paramètres Windows (autounattend.xml) enregistrés.")

        dlg = WinTweaksDialog(self._win_tweaks, _applied)
        dlg.present(self)

    def sync_sensitivity(self) -> None:
        self._sync_action_sensitivity()

    def _sync_action_sensitivity(self) -> None:
        dev = self._get_device()
        busy = self._is_busy()
        iso_ok = bool(self._iso_path and os.path.isfile(self._iso_path))
        dev_ok = bool(dev and not dev.get("is_system_disk") and not dev.get("read_only"))

        can_run = dev_ok and iso_ok and not busy
        self._btn_dry.set_sensitive(can_run)
        self._btn_flash.set_sensitive(can_run)

        if busy:
            self._action_hint.set_label("Création en cours, veuillez patienter...")
        elif not dev:
            self._action_hint.set_label(
                "Branchez une clé USB ci-dessus pour commencer."
                if self._simple_mode
                else "Sélectionnez un périphérique USB cible ci-dessus."
            )
        elif dev.get("is_system_disk"):
            self._action_hint.set_label("Disque système protégé : écriture bloquée.")
        elif not iso_ok:
            self._action_hint.set_label(
                "Choisissez un fichier .iso ou .img à l’étape 1."
                if self._simple_mode
                else "Sélectionnez une image ISO ou IMG valide."
            )
        else:
            fname = os.path.basename(self._iso_path)
            if self._simple_mode:
                self._action_hint.set_label(
                    f"Prêt : {fname} sera installé sur {dev.get('model', 'la clé USB')} (le contenu actuel sera effacé)."
                )
            else:
                self._action_hint.set_label(
                    f"Prêt : {fname} -> {dev.get('path')} ({format_bytes(dev.get('size_bytes', 0))})"
                )

    def _build_job(self) -> Dict[str, Any]:
        dev = self._get_device()
        assert dev is not None
        os_fam = (self._iso_info or {}).get("os_family", "unknown")
        write_mode = self._current_write_mode()
        fs = self._current_fs()
        persist_idx = self._combo_persist_type.get_selected()
        persist_type = self._persist_types[persist_idx] if 0 <= persist_idx < len(self._persist_types) else "casper-rw"

        return {
            "action": "flash_direct",
            "device": dev["path"],
            "image_path": self._iso_path,
            "scheme": self._current_scheme(),
            "filesystem": fs,
            "write_mode": write_mode,
            "volume_label": sanitize_volume_label(self._entry_label.get_text(), fs),
            "quick_format": True if self._simple_mode else self._sw_quick.get_active(),
            "persistence_gb": float(self._spin_persist.get_value()) if (write_mode == "iso" and os_fam != "windows") else 0.0,
            "persistence_type": persist_type,
            "driver_dir": "" if self._simple_mode else self._driver_dir,
            "windows_tweaks": self._win_tweaks if (write_mode == "iso" and os_fam == "windows") else {"enabled": False},
        }

    def _trigger_flash(self, dry_run: bool) -> None:
        dev = self._get_device()
        if not dev or dev.get("is_system_disk"):
            return
        if not self._iso_path or not os.path.isfile(self._iso_path):
            self._on_toast("Fichier introuvable.")
            return

        job = self._build_job()
        if dry_run:
            self._on_start_job(job, True)
            return

        op_label = (
            "Création de clé démarrable"
            if self._simple_mode
            else f"Flash direct ({job['write_mode'].upper()} · {job['scheme'].upper()} · {job['filesystem'].upper()})"
        )
        present_confirm_destroy_dialog(
            self,
            dev,
            op_label,
            f"Fichier à installer : {os.path.basename(self._iso_path)}",
            lambda: self._on_start_job(job, False),
            simple_mode=self._simple_mode,
        )
