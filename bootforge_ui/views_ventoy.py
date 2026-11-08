"""Multi-boot (Ventoy) native GTK4/Libadwaita view."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, Gio, Gtk

from engine.disk_utils import plan_partition_layout, sanitize_volume_label
from engine.i18n import t
from engine.ventoy_manager import build_ventoy_json
from bootforge_ui.backend import read_ventoy_state_on_device, run_async
from bootforge_ui.theme import esc, format_bytes, make_icon_button, set_button_text
from bootforge_ui.widgets import PartitionMapWidget, present_confirm_destroy_dialog


class MultiBootView(Gtk.ScrolledWindow):
    """Multi-system USB builder (Ventoy) adapting between Simple and Advanced modes."""

    def __init__(
        self,
        get_device: Callable[[], Optional[Dict[str, Any]]],
        is_busy: Callable[[], bool],
        on_start_job: Callable[[Dict[str, Any], bool], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._get_device = get_device
        self._is_busy = is_busy
        self._on_start_job = on_start_job
        self._on_log = on_log
        self._on_toast = on_toast

        self._simple_mode = True
        self._syncing_controls = False
        self._last_read_device = ""
        self._entries: List[Dict[str, Any]] = []
        self._remove_relpaths: List[str] = []
        self._action_mode = "ventoy_install"

        self._clamp = Adw.Clamp(
            maximum_size=780,
            margin_top=14,
            margin_bottom=18,
            margin_start=16,
            margin_end=16,
        )
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        self._cols = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._cols.set_homogeneous(False)

        # ================= COLUMN A: Operation & Rules =================
        self._left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        left_col = self._left_col

        self._grp_action = Adw.PreferencesGroup()

        self._rad_apply = Gtk.CheckButton()
        self._rad_update = Gtk.CheckButton(group=self._rad_apply)
        self._rad_install = Gtk.CheckButton(group=self._rad_apply)
        self._rad_install.set_active(True)

        self._row_apply = Adw.ActionRow(use_markup=False)
        self._row_apply.add_prefix(self._rad_apply)
        self._row_apply.set_activatable_widget(self._rad_apply)

        self._row_update = Adw.ActionRow(
            use_markup=False,
            title="Mettre à jour Ventoy uniquement",
            subtitle="Actualise le chargeur VTOYEFI sans toucher aux images ISO déjà copiées",
        )
        self._row_update.add_prefix(self._rad_update)
        self._row_update.set_activatable_widget(self._rad_update)

        self._row_install = Adw.ActionRow(use_markup=False)
        self._row_install.add_prefix(self._rad_install)
        self._row_install.set_activatable_widget(self._rad_install)

        for r in (self._row_apply, self._row_update, self._row_install):
            self._grp_action.add(r)

        self._rad_apply.connect("toggled", lambda *_: self._on_action_toggled())
        self._rad_update.connect("toggled", lambda *_: self._on_action_toggled())
        self._rad_install.connect("toggled", lambda *_: self._on_action_toggled())
        left_col.append(self._grp_action)

        # Advanced-only container settings
        self._grp_cont = Adw.PreferencesGroup(title=esc("Paramètres du conteneur Ventoy"))
        self._combo_scheme = Adw.ComboRow(use_markup=False, title="Table de partitions")
        self._combo_scheme.set_model(Gtk.StringList.new(["GPT (UEFI moderne)", "MBR (BIOS / UEFI)"]))
        self._combo_scheme.set_selected(0)
        self._combo_scheme.connect("notify::selected", lambda *_: self.refresh_partition_map())
        self._grp_cont.add(self._combo_scheme)

        self._sw_sb = Adw.SwitchRow(
            use_markup=False,
            title="Prise en charge Secure Boot (VTOYEFI)",
            subtitle="Recommandé pour démarrer sur les ordinateurs UEFI récents",
        )
        self._sw_sb.set_active(True)
        self._grp_cont.add(self._sw_sb)

        self._entry_label = Adw.EntryRow(use_markup=False, title="Nom du volume (exFAT)")
        self._entry_label.set_text("Ventoy")
        self._grp_cont.add(self._entry_label)

        self._spin_reserve = Adw.SpinRow.new_with_range(0, 16384, 256)
        self._spin_reserve.set_use_markup(False)
        self._spin_reserve.set_title("Espace non alloué en fin de clé (Mio)")
        self._spin_reserve.set_subtitle("0 = utiliser toute la capacité de la clé")
        self._spin_reserve.set_value(0)
        self._spin_reserve.connect("notify::value", lambda *_: self.refresh_partition_map())
        self._grp_cont.add(self._spin_reserve)
        left_col.append(self._grp_cont)

        # Global rules (Simple summary switch vs full ventoy.json controls)
        self._grp_rules = Adw.PreferencesGroup()

        self._sw_win_simple = Adw.SwitchRow(
            use_markup=False,
            title="Faciliter l’installation de Windows 11",
            subtitle="Évite les blocages matériels (TPM / Secure Boot) et l’obligation de compte Microsoft",
        )
        self._sw_win_simple.set_active(True)
        self._sw_win_simple.connect("notify::active", self._on_simple_win_toggled)
        self._grp_rules.add(self._sw_win_simple)

        self._sw_win_hw = Adw.SwitchRow(
            use_markup=False,
            title="Ignorer TPM 2.0, Secure Boot et RAM",
            subtitle="VTOY_WIN11_BYPASS_CHECK dans ventoy.json",
        )
        self._sw_win_hw.set_active(True)
        self._sw_win_hw.connect("notify::active", lambda *_: self._sync_simple_win_switch())

        self._sw_win_nro = Adw.SwitchRow(
            use_markup=False,
            title="Autoriser un compte local Windows 11",
            subtitle="VTOY_WIN11_BYPASS_NRO dans ventoy.json",
        )
        self._sw_win_nro.set_active(True)
        self._sw_win_nro.connect("notify::active", lambda *_: self._sync_simple_win_switch())

        self._sw_tree = Adw.SwitchRow(
            use_markup=False,
            title="Afficher le menu en arborescence",
            subtitle="VTOY_DEFAULT_MENU_MODE (TreeView)",
        )
        self._sw_filter = Adw.SwitchRow(
            use_markup=False,
            title="Masquer les fichiers cachés (._*)",
            subtitle="VTOY_FILT_DOT_UNDERSCORE_FILE",
        )
        self._sw_filter.set_active(True)
        self._spin_timeout = Adw.SpinRow.new_with_range(0, 120, 5)
        self._spin_timeout.set_use_markup(False)
        self._spin_timeout.set_title("Délai de démarrage automatique (s)")
        self._spin_timeout.set_subtitle("0 = attendre le choix manuel au menu")
        self._spin_timeout.set_value(0)

        for w in (self._sw_win_hw, self._sw_win_nro, self._sw_tree, self._sw_filter, self._spin_timeout):
            self._grp_rules.add(w)
        left_col.append(self._grp_rules)
        self._cols.append(left_col)

        # ================= COLUMN B: Systems Queue =================
        self._right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right_col = self._right_col

        queue_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        qtitle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._q_title_lbl = Gtk.Label(xalign=0)
        self._q_title_lbl.add_css_class("heading")
        self._q_sub_lbl = Gtk.Label(xalign=0, wrap=True)
        self._q_sub_lbl.add_css_class("caption")
        self._q_sub_lbl.add_css_class("dim-label")
        qtitle.append(self._q_title_lbl)
        qtitle.append(self._q_sub_lbl)
        qtitle.set_hexpand(True)
        queue_hdr.append(qtitle)

        self._btn_read_usb = make_icon_button("Lire la clé", "view-refresh-symbolic")
        self._btn_read_usb.connect("clicked", lambda *_: self._read_usb_state(notify=True))

        self._btn_json = make_icon_button("Aperçu JSON", "utilities-terminal-symbolic")
        self._btn_json.connect("clicked", self._open_json_preview)

        self._btn_add = make_icon_button("Ajouter", "list-add-symbolic", ("suggested-action",))
        self._btn_add.connect("clicked", self._pick_isos)

        queue_hdr.append(self._btn_read_usb)
        queue_hdr.append(self._btn_json)
        queue_hdr.append(self._btn_add)
        right_col.append(queue_hdr)

        self._queue_scroll = Gtk.ScrolledWindow()
        self._queue_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._queue_scroll.set_vexpand(False)
        self._queue_scroll.set_min_content_height(150)

        self._queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")

        self._empty_status = Adw.StatusPage(
            icon_name="media-optical-symbolic",
            title="Aucun système ajouté",
            description="Ajoutez vos fichiers .iso (par exemple Windows 11 et Ubuntu) avec le bouton ci-dessus.",
        )
        self._empty_status.add_css_class("compact")

        self._queue_box.append(self._empty_status)
        self._queue_box.append(self._list_box)
        self._list_box.set_visible(False)

        self._queue_scroll.set_child(self._queue_box)
        right_col.append(self._queue_scroll)
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

        # Footer Actions
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._action_hint = Gtk.Label(xalign=0, wrap=True)
        self._action_hint.add_css_class("caption")
        self._action_hint.add_css_class("dim-label")
        self._action_hint.set_hexpand(True)
        action_box.append(self._action_hint)

        self._btn_dry = make_icon_button("Simuler", "system-run-symbolic")
        self._btn_dry.connect("clicked", lambda *_: self._trigger_ventoy(dry_run=True))

        self._btn_run = make_icon_button("Préparer la clé", "media-playback-start-symbolic", ("suggested-action",))
        self._btn_run.connect("clicked", lambda *_: self._trigger_ventoy(dry_run=False))

        action_box.append(self._btn_dry)
        action_box.append(self._btn_run)
        root_box.append(action_box)

        self._clamp.set_child(root_box)
        self.set_child(self._clamp)
        self.set_simple_mode(True)
        self._on_device_changed_reset()

    def apply_language(self) -> None:
        """Update localized strings."""
        set_button_text(self._btn_read_usb, t("ventoy_btn_read_usb"))
        set_button_text(self._btn_json, t("ventoy_btn_json"))
        set_button_text(self._btn_dry, t("direct_btn_dry"))
        self._sw_win_simple.set_title(t("ventoy_s_win_tweaks_row"))
        self._sw_win_simple.set_subtitle(t("ventoy_s_win_tweaks_sub"))
        self._row_update.set_title(t("ventoy_a_act_update_title"))
        self._row_update.set_subtitle(t("ventoy_a_act_update_sub"))
        self.set_simple_mode(self._simple_mode)

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple
        rules_target = self._left_col if simple else self._right_col
        cur_parent = self._grp_rules.get_parent()
        if cur_parent is not rules_target:
            if cur_parent is not None:
                cur_parent.remove(self._grp_rules)
            rules_target.append(self._grp_rules)

        if simple:
            self._clamp.set_maximum_size(780)
            self._cols.set_orientation(Gtk.Orientation.VERTICAL)
            self._cols.set_homogeneous(False)

            self._grp_action.set_title(esc(t("ventoy_s_step1_title")))
            self._grp_action.set_description(esc(t("ventoy_s_step1_desc")))
            self._row_apply.set_title(t("ventoy_s_act_apply_title"))
            self._row_apply.set_subtitle(t("ventoy_s_act_apply_sub"))
            self._row_install.set_title(t("ventoy_s_act_install_title"))
            self._row_install.set_subtitle(t("ventoy_s_act_install_sub"))

            self._grp_rules.set_title(esc(t("ventoy_s_step2_title")))
            self._grp_rules.set_description(esc(""))

            self._q_title_lbl.set_label(t("ventoy_s_step3_title"))
            self._q_sub_lbl.set_label(t("ventoy_s_step3_sub"))
            self._queue_scroll.set_vexpand(False)
            self._queue_scroll.set_min_content_height(140)
            set_button_text(self._btn_add, t("ventoy_btn_add_simple"))
            set_button_text(self._btn_run, t("ventoy_btn_run_simple"))
            self._empty_status.set_description(t("ventoy_empty_title"))
        else:
            self._clamp.set_maximum_size(1200)
            self._cols.set_orientation(Gtk.Orientation.HORIZONTAL)
            self._cols.set_homogeneous(True)

            self._grp_action.set_title(esc(t("ventoy_a_step1_title")))
            self._grp_action.set_description(esc(t("ventoy_a_step1_desc")))
            self._row_apply.set_title(t("ventoy_a_act_apply_title"))
            self._row_apply.set_subtitle(t("ventoy_a_act_apply_sub"))
            self._row_install.set_title(t("ventoy_a_act_install_title"))
            self._row_install.set_subtitle(t("ventoy_a_act_install_sub"))

            self._grp_rules.set_title(esc(t("ventoy_a_step2_title")))
            self._grp_rules.set_description(esc(t("ventoy_a_step2_desc")))

            self._q_title_lbl.set_label(t("ventoy_a_step3_title"))
            self._q_sub_lbl.set_label(t("ventoy_a_step3_sub"))
            self._queue_scroll.set_vexpand(False)
            self._queue_scroll.set_min_content_height(150)
            set_button_text(self._btn_add, t("ventoy_btn_add_adv"))
            set_button_text(self._btn_run, t("ventoy_btn_run_adv"))
            self._empty_status.set_description(t("ventoy_empty_title"))

        self._row_update.set_visible(not simple)
        if simple and self._action_mode == "ventoy_update":
            self._rad_apply.set_active(True)

        self._grp_cont.set_visible(not simple)
        self._sw_win_simple.set_visible(simple)
        for w in (self._sw_win_hw, self._sw_win_nro, self._sw_tree, self._sw_filter, self._spin_timeout):
            w.set_visible(not simple)

        self._btn_json.set_visible(not simple)
        self._part_frame.set_visible(not simple)
        self._btn_dry.set_visible(not simple)

        self._rebuild_queue_list()
        if not simple:
            self.refresh_partition_map()
        self.sync_sensitivity()

    def _on_simple_win_toggled(self, sw: Adw.SwitchRow, _pspec: Any) -> None:
        if self._syncing_controls:
            return
        active = sw.get_active()
        self._syncing_controls = True
        try:
            self._sw_win_hw.set_active(active)
            self._sw_win_nro.set_active(active)
        finally:
            self._syncing_controls = False

    def _sync_simple_win_switch(self) -> None:
        if self._syncing_controls:
            return
        self._syncing_controls = True
        try:
            self._sw_win_simple.set_active(self._sw_win_hw.get_active() or self._sw_win_nro.get_active())
        finally:
            self._syncing_controls = False

    def on_device_changed(self) -> None:
        self._on_device_changed_reset()
        dev = self._get_device()
        if dev and dev.get("ventoy_detected"):
            if dev["path"] != self._last_read_device:
                self._last_read_device = dev["path"]
                self._read_usb_state(notify=False)
        else:
            self._last_read_device = ""

    def _on_device_changed_reset(self) -> None:
        dev = self._get_device()
        has_ventoy = bool(dev and dev.get("ventoy_detected"))
        self._row_apply.set_sensitive(has_ventoy)
        self._row_update.set_sensitive(has_ventoy)
        self._btn_read_usb.set_sensitive(has_ventoy and not self._is_busy())
        if has_ventoy:
            self._rad_apply.set_active(True)
        else:
            self._rad_install.set_active(True)
            self._entries = [e for e in self._entries if not e.get("on_usb")]
            self._remove_relpaths.clear()
            self._rebuild_queue_list()
        if not self._simple_mode:
            self.refresh_partition_map()
        self.sync_sensitivity()

    def _on_action_toggled(self) -> None:
        if self._rad_apply.get_active():
            self._action_mode = "ventoy_apply"
        elif self._rad_update.get_active() and not self._simple_mode:
            self._action_mode = "ventoy_update"
        else:
            self._action_mode = "ventoy_install"

        is_install = self._action_mode == "ventoy_install"
        self._combo_scheme.set_sensitive(is_install)
        self._sw_sb.set_sensitive(is_install)
        self._entry_label.set_sensitive(is_install)
        self._spin_reserve.set_sensitive(is_install)
        self.sync_sensitivity()

    def refresh_partition_map(self) -> None:
        dev = self._get_device()
        disk_bytes = int(dev.get("size_bytes", 32 * 1024**3)) if dev else 32 * 1024**3
        scheme = "gpt" if self._combo_scheme.get_selected() == 0 else "mbr"
        reserve = int(self._spin_reserve.get_value())
        segs = plan_partition_layout(
            disk_bytes,
            "ventoy",
            scheme=scheme,
            filesystem="exfat",
            ventoy_reserve_mb=reserve,
        )
        badge = f"{scheme.upper()} · VTOYEFI" + (" SecureBoot" if self._sw_sb.get_active() else "")
        self._part_map.set_layout(segs, title="Partitions Ventoy (exFAT + VTOYEFI)", badge=badge)

    def _pick_isos(self, *_args: Any) -> None:
        dlg = Gtk.FileDialog(title="Ajouter des fichiers .iso ou .img")
        ff = Gtk.FileFilter()
        ff.set_name("Images disque (ISO, IMG, WIM, VHD)")
        for pat in ("*.iso", "*.ISO", "*.img", "*.IMG", "*.wim", "*.vhd", "*.vhdx"):
            ff.add_pattern(pat)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dlg.set_filters(filters)

        win = self.get_root()
        assert isinstance(win, Gtk.Window)

        def _done(source: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                files = source.open_multiple_finish(res)
                added = 0
                for idx in range(files.get_n_items()):
                    f = files.get_item(idx)
                    if isinstance(f, Gio.File) and f.get_path():
                        self.add_iso_external(f.get_path() or "")
                        added += 1
                if added > 0:
                    self._on_toast(f"{added} fichier(s) ajouté(s).")
            except Exception:
                pass

        dlg.open_multiple(win, None, _done)

    def add_iso_external(self, file_path: str) -> None:
        if not os.path.isfile(file_path):
            return
        name = os.path.basename(file_path)
        rel_path = "/" + name
        if any(e["rel_path"] == rel_path for e in self._entries):
            return
        lower = name.lower()
        is_win = any(k in lower for k in ("win11", "win10", "windows", "server"))
        is_kali = any(k in lower for k in ("kali", "debian", "tails"))
        self._entries.append(
            {
                "rel_path": rel_path,
                "name": name,
                "source_path": file_path,
                "on_usb": False,
                "size_bytes": os.path.getsize(file_path),
                "os_hint": "windows" if is_win else "linux",
                "alias": "",
                "default_img": False,
                "unattend_profile": "bypass" if is_win else "none",
                "persist_mb": 0,
                "persist_label": "persistence" if is_kali else "casper-rw",
            }
        )
        self._rebuild_queue_list()

    def _read_usb_state(self, notify: bool = True) -> None:
        dev = self._get_device()
        if not dev or not dev.get("ventoy_detected"):
            return
        self._last_read_device = dev["path"]
        self._btn_read_usb.set_sensitive(False)
        self._on_log("info", f"Lecture de la partition Ventoy sur {dev['path']}...")

        def _do() -> Dict[str, Any]:
            return read_ventoy_state_on_device(dev["path"])

        def _ok(state: Dict[str, Any]) -> None:
            self._btn_read_usb.set_sensitive(True)
            vjson = state.get("ventoy_json") or {}
            alias_map = {a["image"]: a["alias"] for a in vjson.get("menu_alias") or [] if "image" in a and "alias" in a}
            auto_map = {a["image"]: a["template"] for a in vjson.get("auto_install") or [] if "image" in a}
            pers_map = {p["image"]: p["backend"] for p in vjson.get("persistence") or [] if "image" in p}
            ctrl = {}
            for c in vjson.get("control") or []:
                ctrl.update(c)
            def_img = ctrl.get("VTOY_DEFAULT_IMAGE", "")

            usb_items: List[Dict[str, Any]] = []
            for im in state.get("images") or []:
                rel = im["rel_path"]
                usb_items.append(
                    {
                        "rel_path": rel,
                        "name": im["name"],
                        "source_path": None,
                        "on_usb": True,
                        "size_bytes": int(im.get("size_bytes") or 0),
                        "os_hint": im.get("os_hint", "linux"),
                        "alias": alias_map.get(rel, ""),
                        "default_img": (rel == def_img),
                        "unattend_profile": "existing" if rel in auto_map else "none",
                        "existing_template": auto_map.get(rel, ""),
                        "persist_mb": 0,
                        "existing_persist": pers_map.get(rel, ""),
                        "persist_label": "persistence" if "kali" in im["name"].lower() or "debian" in im["name"].lower() else "casper-rw",
                    }
                )
            queued = [e for e in self._entries if not e.get("on_usb")]
            self._entries = usb_items + queued
            self._remove_relpaths.clear()
            self._rebuild_queue_list()
            if notify:
                self._on_toast(f"Clé lue : {len(usb_items)} système(s) trouvé(s).")

        def _err(msg: str) -> None:
            self._btn_read_usb.set_sensitive(True)
            if notify:
                self._on_toast(msg)
            else:
                self._on_log("warn", f"Lecture Ventoy ignorée : {msg}")

        run_async(_do, _ok, _err)

    def _rebuild_queue_list(self) -> None:
        while child := self._list_box.get_first_child():
            self._list_box.remove(child)

        has_items = len(self._entries) > 0
        self._empty_status.set_visible(not has_items)
        self._list_box.set_visible(has_items)

        for entry in list(self._entries):
            loc_txt = (
                ("Déjà sur la clé" if entry["on_usb"] else "Sera copié")
                if self._simple_mode
                else ("Déjà sur Ventoy" if entry["on_usb"] else "À copier vers /")
            )
            alias_txt = f" · {entry['alias']}" if entry.get("alias") else ""
            row = Adw.ExpanderRow(
                use_markup=False,
                title=entry["name"],
                subtitle=f"{format_bytes(entry['size_bytes'])} · {loc_txt}{alias_txt}",
            )
            row.set_icon_name("computer-symbolic" if entry["os_hint"] == "windows" else "applications-system-symbolic")

            badge_lbl = t("ventoy_item_on_usb") if entry["on_usb"] else t("ventoy_item_queued")
            badge = Gtk.Label(label=badge_lbl)
            badge.add_css_class("bf-status-pill")
            badge.add_css_class("bf-pill-ok" if entry["on_usb"] else "bf-pill-cyan")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=t("ventoy_item_remove"))
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.connect("clicked", lambda _b, item=entry: self._remove_entry(item))
            row.add_suffix(del_btn)

            alias_row = Adw.EntryRow(
                use_markup=False,
                title=t("ventoy_item_alias"),
            )
            alias_row.set_text(entry.get("alias", ""))
            alias_row.connect("changed", lambda e, item=entry: item.__setitem__("alias", e.get_text()))
            row.add_row(alias_row)

            def_row = Adw.SwitchRow(
                use_markup=False,
                title=t("ventoy_item_default"),
            )
            def_row.set_active(bool(entry.get("default_img", False)))

            def _toggle_def(sw: Adw.SwitchRow, _pspec: Any, item=entry) -> None:
                if sw.get_active():
                    for other in self._entries:
                        other["default_img"] = False
                    item["default_img"] = True
                else:
                    item["default_img"] = False

            def_row.connect("notify::active", _toggle_def)
            row.add_row(def_row)

            if entry["os_hint"] == "windows":
                if self._simple_mode:
                    win_sw = Adw.SwitchRow(
                        use_markup=False,
                        title="Faciliter l’installation Windows",
                        subtitle="Évite les blocages matériels et le compte Microsoft obligatoire",
                    )
                    win_sw.set_active(entry.get("unattend_profile", "bypass") != "none")
                    win_sw.connect(
                        "notify::active",
                        lambda sw, _ps, item=entry: item.__setitem__(
                            "unattend_profile", "bypass" if sw.get_active() else "none"
                        ),
                    )
                    row.add_row(win_sw)
                else:
                    win_combo = Adw.ComboRow(use_markup=False, title="Profil autounattend.xml")
                    profiles = ["bypass", "bypass_local", "none"]
                    win_combo.set_model(
                        Gtk.StringList.new(
                            [
                                "Contournement TPM/SecureBoot + Sans compte Microsoft",
                                "Contournement complet + Compte Administrateur Local",
                                "Aucun XML (règles globales uniquement)",
                            ]
                        )
                    )
                    cur = entry.get("unattend_profile", "bypass")
                    win_combo.set_selected(profiles.index(cur) if cur in profiles else 0)
                    win_combo.connect(
                        "notify::selected",
                        lambda c, _ps, item=entry, ps=profiles: item.__setitem__(
                            "unattend_profile", ps[c.get_selected()] if 0 <= c.get_selected() < len(ps) else "none"
                        ),
                    )
                    row.add_row(win_combo)
            else:
                if self._simple_mode:
                    spin_gb = Adw.SpinRow.new_with_range(0, 16, 1)
                    spin_gb.set_use_markup(False)
                    spin_gb.set_title("Espace pour garder vos fichiers (Go)")
                    spin_gb.set_subtitle(entry.get("existing_persist") or "0 = démarrage classique sans sauvegarde")
                    spin_gb.set_value(round(float(entry.get("persist_mb", 0)) / 1024.0))
                    spin_gb.connect(
                        "notify::value",
                        lambda s, _ps, item=entry: item.__setitem__("persist_mb", int(s.get_value()) * 1024),
                    )
                    row.add_row(spin_gb)
                else:
                    spin_p = Adw.SpinRow.new_with_range(0, 16384, 512)
                    spin_p.set_use_markup(False)
                    spin_p.set_title("Conteneur persistant .dat (Mio)")
                    spin_p.set_subtitle(entry.get("existing_persist") or "0 = sans persistance")
                    spin_p.set_value(float(entry.get("persist_mb", 0)))
                    spin_p.connect(
                        "notify::value",
                        lambda s, _ps, item=entry: item.__setitem__("persist_mb", int(s.get_value())),
                    )
                    row.add_row(spin_p)

                    ptypes = ["casper-rw", "persistence"]
                    pcombo = Adw.ComboRow(use_markup=False, title="Label du conteneur ext4")
                    pcombo.set_model(Gtk.StringList.new(["casper-rw (Ubuntu / Mint)", "persistence (Kali / Debian)"]))
                    cur_pt = entry.get("persist_label", "casper-rw")
                    pcombo.set_selected(ptypes.index(cur_pt) if cur_pt in ptypes else 0)
                    pcombo.connect(
                        "notify::selected",
                        lambda c, _ps, item=entry, pts=ptypes: item.__setitem__(
                            "persist_label", pts[c.get_selected()] if 0 <= c.get_selected() < len(pts) else "casper-rw"
                        ),
                    )
                    row.add_row(pcombo)

            self._list_box.append(row)

    def _remove_entry(self, entry: Dict[str, Any]) -> None:
        if entry.get("on_usb"):
            self._remove_relpaths.append(entry["rel_path"])
        self._entries = [e for e in self._entries if e["rel_path"] != entry["rel_path"]]
        self._rebuild_queue_list()

    def _collect_ventoy_payload(self) -> Dict[str, Any]:
        menu_alias = []
        auto_install = []
        persistence = []
        unattend_profiles = []
        new_persistences = []
        add_isos = []
        default_img = ""

        for idx, e in enumerate(self._entries, start=1):
            rel = e["rel_path"]
            if e.get("source_path") and not e.get("on_usb"):
                add_isos.append(e["source_path"])
            if e.get("alias", "").strip():
                menu_alias.append({"image": rel, "alias": e["alias"].strip()})
            if e.get("default_img"):
                default_img = rel

            if e["os_hint"] == "windows":
                prof = e.get("unattend_profile", "none")
                if prof in ("bypass", "bypass_local"):
                    fname = f"win_profile_{idx}.xml"
                    auto_install.append({"image": rel, "template": f"/ventoy/scripts/{fname}"})
                    unattend_profiles.append(
                        {
                            "filename": fname,
                            "options": {
                                "bypass_tpm_secureboot_ram": True,
                                "bypass_microsoft_account": True,
                                "disable_bitlocker": True,
                                "disable_telemetry": True,
                                "skip_product_key": True,
                                "create_local_account": (prof == "bypass_local"),
                                "local_username": "Utilisateur",
                                "locale": "fr-FR",
                            },
                        }
                    )
                elif prof == "existing" and e.get("existing_template"):
                    auto_install.append({"image": rel, "template": e["existing_template"]})
            else:
                pmb = int(e.get("persist_mb") or 0)
                if pmb >= 128:
                    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in os.path.splitext(e["name"])[0])[:36]
                    dat_name = f"{safe_stem}.dat"
                    lbl = e.get("persist_label", "casper-rw")
                    new_persistences.append(
                        {
                            "filename": dat_name,
                            "size_mb": pmb,
                            "label": lbl,
                            "with_persistence_conf": (lbl == "persistence"),
                        }
                    )
                    persistence.append({"image": rel, "backend": f"/ventoy/persistence/{dat_name}"})
                elif e.get("existing_persist"):
                    persistence.append({"image": rel, "backend": e["existing_persist"]})

        v_config = {
            "win11_bypass_check": self._sw_win_hw.get_active(),
            "win11_bypass_nro": self._sw_win_nro.get_active(),
            "tree_view": self._sw_tree.get_active(),
            "filter_dot_files": self._sw_filter.get_active(),
            "menu_timeout": int(self._spin_timeout.get_value()),
            "default_image": default_img,
            "menu_alias": menu_alias,
            "auto_install": auto_install,
            "persistence": persistence,
        }
        return {
            "ventoy_config": v_config,
            "add_isos": add_isos,
            "remove_isos": list(self._remove_relpaths),
            "create_persistence": new_persistences,
            "unattend_profiles": unattend_profiles,
        }

    def _open_json_preview(self, *_args: Any) -> None:
        payload = self._collect_ventoy_payload()
        doc = build_ventoy_json(payload["ventoy_config"])
        txt = json.dumps(doc, indent=2, ensure_ascii=False)

        dlg = Adw.Dialog()
        dlg.set_title("Aperçu /ventoy/ventoy.json")
        dlg.set_content_width(600)
        dlg.set_content_height(520)

        toolbar = Adw.ToolbarView()
        hdr = Adw.HeaderBar()
        cp = make_icon_button("Copier", "edit-copy-symbolic")

        def _copy(*_a: Any) -> None:
            disp = Gdk.Display.get_default()
            if disp:
                disp.get_clipboard().set(txt)
            self._on_toast("Configuration copiée.")

        cp.connect("clicked", _copy)
        hdr.pack_end(cp)
        toolbar.add_top_bar(hdr)

        scroll = Gtk.ScrolledWindow()
        scroll.set_margin_top(12)
        scroll.set_margin_bottom(12)
        scroll.set_margin_start(14)
        scroll.set_margin_end(14)
        tv = Gtk.TextView(editable=False, cursor_visible=False)
        tv.set_monospace(True)
        tv.add_css_class("bf-code-view")
        tv.get_buffer().set_text(txt)
        scroll.set_child(tv)
        toolbar.set_content(scroll)
        dlg.set_child(toolbar)
        dlg.present(self)

    def sync_sensitivity(self) -> None:
        dev = self._get_device()
        busy = self._is_busy()
        dev_ok = bool(dev and not dev.get("is_system_disk") and not dev.get("read_only"))
        self._btn_dry.set_sensitive(dev_ok and not busy)
        self._btn_run.set_sensitive(dev_ok and not busy)
        self._btn_read_usb.set_sensitive(bool(dev and dev.get("ventoy_detected")) and not busy)

        if self._simple_mode:
            set_button_text(
                self._btn_run,
                "Préparer la clé" if self._action_mode == "ventoy_install" else "Mettre à jour la clé",
            )
        else:
            set_button_text(self._btn_run, "Exécuter Ventoy")

        if not dev:
            self._action_hint.set_label("Branchez une clé USB ci-dessus pour la préparer.")
        elif dev.get("is_system_disk"):
            self._action_hint.set_label("Disque système protégé : écriture bloquée.")
        elif self._simple_mode:
            if self._action_mode == "ventoy_install":
                self._action_hint.set_label(
                    f"La clé {dev.get('model', 'USB')} sera formatée pour accueillir plusieurs systèmes."
                )
            else:
                self._action_hint.set_label(
                    f"Mise à jour des fichiers sur {dev.get('model', 'la clé')} sans effacer vos systèmes existants."
                )
        else:
            mode_txt = {
                "ventoy_install": "Installation complète Ventoy (efface la clé)",
                "ventoy_update": "Mise à jour non destructive VTOYEFI",
                "ventoy_apply": "Synchronisation ISO et ventoy.json",
            }[self._action_mode]
            self._action_hint.set_label(f"{mode_txt} sur {dev.get('path')}")

    def _trigger_ventoy(self, dry_run: bool) -> None:
        dev = self._get_device()
        if not dev or dev.get("is_system_disk"):
            return

        payload = self._collect_ventoy_payload()
        scheme = "gpt" if (self._simple_mode or self._combo_scheme.get_selected() == 0) else "mbr"
        job = {
            "action": self._action_mode,
            "device": dev["path"],
            "scheme": scheme,
            "secure_boot": True if self._simple_mode else self._sw_sb.get_active(),
            "reserve_mb": 0 if self._simple_mode else int(self._spin_reserve.get_value()),
            "volume_label": "Ventoy" if self._simple_mode else sanitize_volume_label(self._entry_label.get_text(), "exfat"),
            **payload,
        }

        if dry_run:
            self._on_start_job(job, True)
            return

        if self._action_mode == "ventoy_install":
            op_label = "Préparation multi-systèmes" if self._simple_mode else f"Installation Ventoy ({scheme.upper()})"
            present_confirm_destroy_dialog(
                self,
                dev,
                op_label,
                f"{len(payload['add_isos'])} système(s) à copier après préparation",
                lambda: self._on_start_job(job, False),
                simple_mode=self._simple_mode,
            )
        else:
            self._on_start_job(job, False)
