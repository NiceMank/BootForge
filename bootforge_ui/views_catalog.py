"""Official OS download catalog native GTK4/Libadwaita view."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, Gtk, Pango

from bootforge_ui.backend import OFFICIAL_CATALOG, ActiveProcessController, default_downloads_dir
from bootforge_ui.theme import esc, make_icon_button


class CatalogView(Gtk.ScrolledWindow):
    """Official HTTPS OS catalog adapting between Simple and Advanced modes."""

    def __init__(
        self,
        proc_ctrl: ActiveProcessController,
        on_telemetry: Callable[[Dict[str, Any]], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        on_busy_change: Callable[[bool], None],
        on_use_in_direct: Callable[[str], None],
        on_use_in_ventoy: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._proc = proc_ctrl
        self._on_telemetry = on_telemetry
        self._on_log = on_log
        self._on_toast = on_toast
        self._on_busy_change = on_busy_change
        self._on_use_in_direct = on_use_in_direct
        self._on_use_in_ventoy = on_use_in_ventoy

        self._simple_mode = True
        self._dest_dir = str(default_downloads_dir())
        self._active_category = "Toutes"
        self._downloaded_paths: Dict[str, str] = {}
        self._dl_buttons: List[Gtk.Button] = []
        self._cat_buttons: List[Gtk.ToggleButton] = []

        self._clamp = Adw.Clamp(
            maximum_size=860,
            margin_top=14,
            margin_bottom=18,
            margin_start=16,
            margin_end=16,
        )
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        self._linked = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._linked.add_css_class("linked")
        top_bar.append(self._linked)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_bar.append(spacer)

        self._dest_lbl = Gtk.Label(label=f"Dossier : {self._dest_dir}")
        self._dest_lbl.add_css_class("caption")
        self._dest_lbl.add_css_class("dim-label")
        self._dest_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._dest_lbl.set_max_width_chars(36)
        top_bar.append(self._dest_lbl)

        folder_btn = make_icon_button("Dossier...", "folder-open-symbolic")
        folder_btn.connect("clicked", self._pick_dest_folder)
        top_bar.append(folder_btn)

        root_box.append(top_bar)

        self._group = Adw.PreferencesGroup()
        root_box.append(self._group)

        self._clamp.set_child(root_box)
        self.set_child(self._clamp)
        self.set_simple_mode(True)

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple
        self._clamp.set_maximum_size(860 if simple else 1100)
        self._active_category = "Tous" if simple else "Toutes"
        self._rebuild_category_buttons()
        self._rebuild_rows()

    def _rebuild_category_buttons(self) -> None:
        while child := self._linked.get_first_child():
            self._linked.remove(child)
        self._cat_buttons.clear()

        cats = (
            ["Tous", "Systèmes courants", "Dépannage", "Sécurité"]
            if self._simple_mode
            else ["Toutes", "Linux Desktop", "Sécurité & Pentest", "Secours & Disque"]
        )
        first_btn: Gtk.ToggleButton | None = None
        for cat in cats:
            btn = Gtk.ToggleButton(label=cat)
            if first_btn is None:
                first_btn = btn
                btn.set_active(True)
            else:
                btn.set_group(first_btn)
            btn.connect("toggled", lambda b, c=cat: self._on_category_toggled(b, c))
            self._linked.append(btn)
            self._cat_buttons.append(btn)

    def _on_category_toggled(self, btn: Gtk.ToggleButton, category: str) -> None:
        if btn.get_active():
            self._active_category = category
            self._rebuild_rows()

    def _pick_dest_folder(self, *_args: Any) -> None:
        dlg = Gtk.FileDialog(title="Choisir le dossier d’enregistrement")
        win = self.get_root()
        assert isinstance(win, Gtk.Window)

        def _done(source: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = source.select_folder_finish(res)
                if f and f.get_path():
                    self._dest_dir = f.get_path() or self._dest_dir
                    self._dest_lbl.set_label(f"Dossier : {self._dest_dir}")
            except Exception:
                pass

        dlg.select_folder(win, None, _done)

    def _open_checksum_uri(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as exc:
            self._on_toast(f"Impossible d’ouvrir le lien : {exc}")

    def _rebuild_rows(self) -> None:
        parent = self._group.get_parent()
        assert isinstance(parent, Gtk.Box)
        parent.remove(self._group)

        if self._simple_mode:
            title = "Systèmes gratuits disponibles"
            desc = "Téléchargement direct depuis les sites officiels. Prêts à copier sur une clé USB."
            cat_key = "simple_category"
            all_key = "Tous"
        else:
            title = "Images officielles vérifiées (HTTPS)"
            desc = "Téléchargement direct depuis les miroirs officiels avec lien vers les sommes SHA-256."
            cat_key = "category"
            all_key = "Toutes"

        self._group = Adw.PreferencesGroup(title=esc(title), description=esc(desc))
        self._dl_buttons.clear()

        items = [
            it
            for it in OFFICIAL_CATALOG
            if self._active_category == all_key or it.get(cat_key) == self._active_category
        ]

        for item in items:
            if self._simple_mode:
                row_title = item["name"]
                row_sub = f"{item['simple_description']} ({item['size_hint']})"
            else:
                row_title = f"{item['name']} · {item['version']}"
                row_sub = f"{item['description']} ({item['architecture']} · {item['size_hint']} · Persistance : {item['persistence_hint']})"

            row = Adw.ActionRow(use_markup=False, title=row_title, subtitle=row_sub)
            row.set_icon_name(
                "security-high-symbolic" if item["category"] == "Sécurité & Pentest" else "applications-system-symbolic"
            )

            if not self._simple_mode:
                badge = Gtk.Label(label=item["category"])
                badge.add_css_class("bf-status-pill")
                badge.add_css_class("bf-pill-cyan")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)

                sha_btn = Gtk.Button(label="SHA-256")
                sha_btn.set_valign(Gtk.Align.CENTER)
                sha_btn.connect("clicked", lambda _b, u=item["checksum_page"]: self._open_checksum_uri(u))
                row.add_suffix(sha_btn)

            saved_path = self._downloaded_paths.get(item["id"])
            if saved_path and os.path.isfile(saved_path):
                btn_use = Gtk.Button(label="Mettre sur une clé" if self._simple_mode else "Flash direct")
                btn_use.set_valign(Gtk.Align.CENTER)
                btn_use.connect("clicked", lambda _b, p=saved_path: self._on_use_in_direct(p))
                btn_vtoy = Gtk.Button(label="+ Multi-systèmes" if self._simple_mode else "+ Ventoy")
                btn_vtoy.add_css_class("suggested-action")
                btn_vtoy.set_valign(Gtk.Align.CENTER)
                btn_vtoy.connect("clicked", lambda _b, p=saved_path: self._on_use_in_ventoy(p))
                row.add_suffix(btn_use)
                row.add_suffix(btn_vtoy)
            else:
                dl_btn = make_icon_button(
                    "Télécharger" if self._simple_mode else "Télécharger ISO",
                    "folder-download-symbolic",
                    ("suggested-action",),
                )
                dl_btn.set_valign(Gtk.Align.CENTER)
                dl_btn.set_sensitive(not self._proc.is_busy())
                dl_btn.connect("clicked", lambda _b, it=item: self._start_download(it))
                row.add_suffix(dl_btn)
                self._dl_buttons.append(dl_btn)

            self._group.add(row)

        parent.append(self._group)

    def sync_sensitivity(self) -> None:
        busy = self._proc.is_busy()
        for b in self._dl_buttons:
            b.set_sensitive(not busy)

    def _start_download(self, item: Dict[str, str]) -> None:
        if self._proc.is_busy():
            self._on_toast("Une opération est déjà en cours.")
            return

        self._on_log("info", f"Téléchargement officiel : {item['url']}")
        self._on_busy_change(True)
        self.sync_sensitivity()

        def _done(ok: bool, path_or_err: str) -> None:
            self._on_busy_change(False)
            self.sync_sensitivity()
            if ok:
                self._downloaded_paths[item["id"]] = path_or_err
                self._rebuild_rows()
                self._on_toast(f"{item['name']} téléchargé : {Path(path_or_err).name}")
            else:
                self._on_toast(f"Échec du téléchargement : {path_or_err}")

        self._proc.run_catalog_download(item, self._dest_dir, self._on_telemetry, _done)
