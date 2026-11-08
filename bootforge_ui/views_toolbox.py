"""USB diagnostics, integrity checks, and factory reset GTK4/Libadwaita view."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, Gio, Gtk

from engine.disk_utils import sanitize_volume_label
from bootforge_ui.backend import ActiveProcessController, launch_qemu_preview
from bootforge_ui.theme import esc, make_icon_button, set_button_text
from bootforge_ui.widgets import present_confirm_destroy_dialog


class ToolboxView(Gtk.ScrolledWindow):
    """USB repair, file verification, hardware diagnostics, and disk cloning tools."""

    def __init__(
        self,
        get_device: Callable[[], Optional[Dict[str, Any]]],
        proc_ctrl: ActiveProcessController,
        on_start_job: Callable[[Dict[str, Any], bool], None],
        on_telemetry: Callable[[Dict[str, Any]], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        on_busy_change: Callable[[bool], None],
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._get_device = get_device
        self._proc = proc_ctrl
        self._on_start_job = on_start_job
        self._on_telemetry = on_telemetry
        self._on_log = on_log
        self._on_toast = on_toast
        self._on_busy_change = on_busy_change
        self._simple_mode = True
        self._hash_done = False

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

        # ================= LEFT / TOP: File Integrity & Health =================
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        self._grp_hash = Adw.PreferencesGroup()
        self._hash_file_entry = Adw.EntryRow(use_markup=False, title="Fichier à vérifier")
        pick_btn = Gtk.Button(label="Choisir...")
        pick_btn.set_valign(Gtk.Align.CENTER)
        pick_btn.connect("clicked", self._pick_hash_file)
        self._hash_file_entry.add_suffix(pick_btn)
        self._grp_hash.add(self._hash_file_entry)

        self._expected_entry = Adw.EntryRow(use_markup=False, title="Code SHA-256 attendu (facultatif)")
        self._grp_hash.add(self._expected_entry)

        self._hash_action_row = Adw.ActionRow(use_markup=False)
        self._btn_calc_hash = make_icon_button("Vérifier", "security-high-symbolic", ("suggested-action",))
        self._btn_calc_hash.set_valign(Gtk.Align.CENTER)
        self._btn_calc_hash.connect("clicked", self._run_hash)
        self._hash_action_row.add_suffix(self._btn_calc_hash)
        self._grp_hash.add(self._hash_action_row)

        self._row_hash_match = Adw.ActionRow(use_markup=False, title="Résultat", subtitle="Aucun calcul lancé")
        self._badge_hash = Gtk.Label(label="En attente")
        self._badge_hash.add_css_class("bf-status-pill")
        self._badge_hash.add_css_class("bf-pill-neutral")
        self._badge_hash.set_valign(Gtk.Align.CENTER)
        self._row_hash_match.add_suffix(self._badge_hash)
        self._row_hash_match.set_visible(False)
        self._grp_hash.add(self._row_hash_match)

        self._row_sha256 = self._make_copyable_hash_row("SHA-256")
        self._row_sha1 = self._make_copyable_hash_row("SHA-1")
        self._row_md5 = self._make_copyable_hash_row("MD5")
        self._grp_hash.add(self._row_sha256)
        self._grp_hash.add(self._row_sha1)
        self._grp_hash.add(self._row_md5)
        left_col.append(self._grp_hash)

        self._grp_health = Adw.PreferencesGroup()
        self._sw_destructive_bb = Adw.SwitchRow(
            use_markup=False,
            title="Test complet en écriture + lecture (-w, efface la clé)",
            subtitle="Permet de détecter les fausses capacités sur clés USB suspectes",
        )
        self._grp_health.add(self._sw_destructive_bb)

        self._row_bb = Adw.ActionRow(use_markup=False)
        self._btn_badblocks = Gtk.Button(label="Vérifier la clé")
        self._btn_badblocks.set_valign(Gtk.Align.CENTER)
        self._btn_badblocks.connect("clicked", self._run_badblocks)
        self._row_bb.add_suffix(self._btn_badblocks)
        self._grp_health.add(self._row_bb)

        self._row_backup = Adw.ActionRow(
            use_markup=False,
            title="Cloner la clé USB vers un fichier",
            subtitle="Export brut .img ou compressé .img.gz",
        )
        self._btn_backup = Gtk.Button(label="Sauvegarder (.img)...")
        self._btn_backup.set_valign(Gtk.Align.CENTER)
        self._btn_backup.connect("clicked", self._run_backup)
        self._row_backup.add_suffix(self._btn_backup)
        self._grp_health.add(self._row_backup)
        left_col.append(self._grp_health)

        self._cols.append(left_col)

        # ================= RIGHT / BOTTOM: Speed, QEMU & Reset =================
        right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        self._grp_diag = Adw.PreferencesGroup()
        self._row_bench = Adw.ActionRow(use_markup=False)
        self._badge_bench = Gtk.Label(label="—")
        self._badge_bench.add_css_class("bf-status-pill")
        self._badge_bench.add_css_class("bf-pill-neutral")
        self._badge_bench.add_css_class("bf-mono")
        self._badge_bench.set_valign(Gtk.Align.CENTER)
        self._btn_bench = Gtk.Button(label="Mesurer")
        self._btn_bench.set_valign(Gtk.Align.CENTER)
        self._btn_bench.connect("clicked", self._run_benchmark)
        self._row_bench.add_suffix(self._badge_bench)
        self._row_bench.add_suffix(self._btn_bench)
        self._grp_diag.add(self._row_bench)

        self._row_qemu = Adw.ActionRow(
            use_markup=False,
            title="Tester le démarrage sans redémarrer (QEMU)",
            subtitle="Ouvre un aperçu virtuel en lecture seule (-snapshot) avec OVMF",
        )
        self._btn_qemu = make_icon_button("Lancer QEMU", "media-playback-start-symbolic")
        self._btn_qemu.set_valign(Gtk.Align.CENTER)
        self._btn_qemu.connect("clicked", self._run_qemu)
        self._row_qemu.add_suffix(self._btn_qemu)
        self._grp_diag.add(self._row_qemu)
        right_col.append(self._grp_diag)

        self._grp_wipe = Adw.PreferencesGroup()
        self._wipe_schemes = ["gpt", "mbr"]
        self._combo_wipe_scheme = Adw.ComboRow(use_markup=False, title="Table de partitions")
        self._combo_wipe_scheme.set_model(Gtk.StringList.new(["GPT (Moderne)", "MBR (Compatibilité étendue)"]))
        self._combo_wipe_scheme.set_selected(0)
        self._grp_wipe.add(self._combo_wipe_scheme)

        self._wipe_fs = ["exfat", "fat32", "ntfs"]
        self._combo_wipe_fs = Adw.ComboRow(use_markup=False, title="Système de fichiers")
        self._combo_wipe_fs.set_model(
            Gtk.StringList.new(["exFAT (Universel)", "FAT32 (Classique)", "NTFS (Windows)"])
        )
        self._combo_wipe_fs.set_selected(0)
        self._grp_wipe.add(self._combo_wipe_fs)

        self._entry_wipe_label = Adw.EntryRow(use_markup=False, title="Nom du volume")
        self._entry_wipe_label.set_text("USB_CLEAN")
        self._grp_wipe.add(self._entry_wipe_label)

        self._row_wipe_act = Adw.ActionRow(use_markup=False)
        self._btn_wipe = Gtk.Button(label="Remettre à neuf")
        self._btn_wipe.add_css_class("destructive-action")
        self._btn_wipe.set_valign(Gtk.Align.CENTER)
        self._btn_wipe.connect("clicked", self._run_wipe)
        self._row_wipe_act.add_suffix(self._btn_wipe)
        self._grp_wipe.add(self._row_wipe_act)

        right_col.append(self._grp_wipe)
        self._cols.append(right_col)
        root_box.append(self._cols)

        self._clamp.set_child(root_box)
        self.set_child(self._clamp)
        self.set_simple_mode(True)
        self.sync_sensitivity()

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple
        if simple:
            self._clamp.set_maximum_size(760)
            self._cols.set_orientation(Gtk.Orientation.VERTICAL)
            self._cols.set_homogeneous(False)

            self._grp_hash.set_title(esc("Vérifier qu’un fichier téléchargé n’est pas abîmé"))
            self._grp_hash.set_description(esc("Optionnel : compare votre fichier ISO avec le code officiel fourni sur le site."))
            self._expected_entry.set_title("Code de vérification du site (facultatif)")
            self._hash_action_row.set_title("Contrôler le fichier")
            self._hash_action_row.set_subtitle("Analyse le fichier sur votre ordinateur")
            set_button_text(self._btn_calc_hash, "Vérifier le fichier")

            self._grp_health.set_title(esc("Vérifier l’état de la clé USB"))
            self._grp_health.set_description(esc("Recherche les erreurs matérielles sans rien effacer."))
            self._sw_destructive_bb.set_active(False)
            self._row_bb.set_title("Contrôle de fiabilité (lecture seule)")
            self._row_bb.set_subtitle("Vérifie que les fichiers peuvent être relus sans erreur")
            self._btn_badblocks.set_label("Tester la clé")

            self._grp_diag.set_title(esc("Vitesse de la clé USB"))
            self._grp_diag.set_description(esc("Mesure la rapidité de lecture de votre clé sur ce port USB."))
            self._row_bench.set_title("Tester la vitesse de lecture")
            self._row_bench.set_subtitle("Sans danger — ne modifie pas vos données")
            self._btn_bench.set_label("Mesurer")

            self._grp_wipe.set_title(esc("Réparer une clé USB (tout effacer)"))
            self._grp_wipe.set_description(
                esc("Si votre clé affiche une capacité réduite ou n’est plus reconnue, ce nettoyage restaure toute sa taille d’origine.")
            )
            self._row_wipe_act.set_title("Remettre la clé comme neuve")
            self._row_wipe_act.set_subtitle("Formate toute la clé en un volume compatible Windows, Mac et Linux")
            self._btn_wipe.set_label("Tout effacer et réparer")
        else:
            self._clamp.set_maximum_size(1180)
            self._cols.set_orientation(Gtk.Orientation.HORIZONTAL)
            self._cols.set_homogeneous(True)

            self._grp_hash.set_title(esc("Vérificateur d’empreintes (SHA-256 / SHA-1 / MD5)"))
            self._grp_hash.set_description(esc("Calcul en streaming et comparaison en temps constant."))
            self._expected_entry.set_title("Empreinte attendue (optionnelle)")
            self._hash_action_row.set_title("Calculer les empreintes du fichier")
            self._hash_action_row.set_subtitle("Génère SHA-256, SHA-1 et MD5")
            set_button_text(self._btn_calc_hash, "Calculer et vérifier")

            self._grp_health.set_title(esc("Contrôle de surface (badblocks) et clone disque"))
            self._grp_health.set_description(esc("Détection de secteurs défectueux ou fausses capacités et sauvegarde brute."))
            self._row_bb.set_title("Analyser les secteurs (/sbin/badblocks -b 4096)")
            self._row_bb.set_subtitle("Mode lecture seule par défaut ; activez -w ci-dessus pour test destructif")
            self._btn_badblocks.set_label("Lancer badblocks")

            self._grp_diag.set_title(esc("Diagnostic matériel et aperçu VM"))
            self._grp_diag.set_description(esc("Mesure de débit séquentiel (256 Mio) et boot virtuel QEMU/OVMF."))
            self._row_bench.set_title("Benchmark lecture séquentielle (256 Mio)")
            self._row_bench.set_subtitle("Évalue la vitesse réelle sur ce port USB")
            self._btn_bench.set_label("Mesurer")

            self._grp_wipe.set_title(esc("Remise à zéro d’usine"))
            self._grp_wipe.set_description(esc("Supprime VTOYEFI, casper-rw ou ISO9660 et recrée une partition unique."))
            self._row_wipe_act.set_title("Effacement et réinitialisation")
            self._row_wipe_act.set_subtitle("Restaure 100 % de la capacité sur une table propre")
            self._btn_wipe.set_label("Effacer et restaurer")

        self._sw_destructive_bb.set_visible(not simple)
        self._row_backup.set_visible(not simple)
        self._row_qemu.set_visible(not simple)
        self._combo_wipe_scheme.set_visible(not simple)
        self._combo_wipe_fs.set_visible(not simple)
        self._entry_wipe_label.set_visible(not simple)

        if self._hash_done:
            self._row_sha1.set_visible(not simple)
            self._row_md5.set_visible(not simple)

    def _make_copyable_hash_row(self, algo: str) -> Adw.ActionRow:
        row = Adw.ActionRow(use_markup=False, title=algo, subtitle="—")
        row.set_visible(False)
        row.set_subtitle_selectable(True)
        btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=f"Copier {algo}")
        btn.add_css_class("flat")
        btn.set_valign(Gtk.Align.CENTER)

        def _copy(*_a: Any, r=row) -> None:
            txt = r.get_subtitle() or ""
            if txt and txt != "—":
                disp = Gdk.Display.get_default()
                if disp:
                    disp.get_clipboard().set(txt)
                self._on_toast(f"{algo} copié.")

        btn.connect("clicked", _copy)
        row.add_suffix(btn)
        return row

    def set_benchmark_result(self, mbps: float) -> None:
        self._badge_bench.set_label(f"{mbps:.1f} Mo/s")
        for cls in ("bf-pill-neutral", "bf-pill-cyan", "bf-pill-ok"):
            self._badge_bench.remove_css_class(cls)
        self._badge_bench.add_css_class("bf-pill-cyan")

    def sync_sensitivity(self) -> None:
        dev = self._get_device()
        busy = self._proc.is_busy()
        dev_ok = bool(dev and not dev.get("is_system_disk") and not dev.get("read_only"))
        self._btn_calc_hash.set_sensitive(not busy)
        self._btn_badblocks.set_sensitive(dev_ok and not busy)
        self._btn_backup.set_sensitive(dev_ok and not busy)
        self._btn_bench.set_sensitive(dev_ok and not busy)
        self._btn_qemu.set_sensitive(dev_ok and not busy)
        self._btn_wipe.set_sensitive(dev_ok and not busy)

    def _pick_hash_file(self, *_args: Any) -> None:
        dlg = Gtk.FileDialog(title="Choisir un fichier à vérifier")
        win = self.get_root()
        assert isinstance(win, Gtk.Window)

        def _done(source: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = source.open_finish(res)
                if f and f.get_path():
                    self._hash_file_entry.set_text(f.get_path() or "")
            except Exception:
                pass

        dlg.open(win, None, _done)

    def _run_hash(self, *_args: Any) -> None:
        fpath = self._hash_file_entry.get_text().strip()
        if not fpath or not os.path.isfile(fpath):
            self._on_toast("Fichier introuvable.")
            return
        if self._proc.is_busy():
            return

        expected = self._expected_entry.get_text().strip()
        self._on_log("info", f"Calcul des empreintes sur {fpath}...")
        self._on_busy_change(True)
        self.sync_sensitivity()

        def _done(res: Optional[Dict[str, Any]], err: Optional[str]) -> None:
            self._on_busy_change(False)
            self.sync_sensitivity()
            if err or not res:
                self._on_toast(err or "Erreur lors du calcul.")
                return
            self._hash_done = True
            hashes = res.get("hashes") or {}
            self._row_sha256.set_subtitle(hashes.get("sha256", "—"))
            self._row_sha1.set_subtitle(hashes.get("sha1", "—"))
            self._row_md5.set_subtitle(hashes.get("md5", "—"))
            self._row_sha256.set_visible(True)
            self._row_sha1.set_visible(not self._simple_mode)
            self._row_md5.set_visible(not self._simple_mode)

            matched = res.get("matched")
            self._row_hash_match.set_visible(True)
            for cls in ("bf-pill-neutral", "bf-pill-ok", "bf-pill-err"):
                self._badge_hash.remove_css_class(cls)
            if matched is True:
                algo = str(res.get("matched_algorithm") or "").upper()
                self._row_hash_match.set_subtitle(f"Le fichier correspond exactement ({algo}).")
                self._badge_hash.set_label("INTACT")
                self._badge_hash.add_css_class("bf-pill-ok")
                self._on_toast("Fichier vérifié et conforme.")
            elif matched is False:
                self._row_hash_match.set_subtitle("Attention : ce fichier ne correspond pas au code attendu.")
                self._badge_hash.set_label("DIFFÉRENT")
                self._badge_hash.add_css_class("bf-pill-err")
                self._on_toast("Attention : empreinte différente !")
            else:
                self._row_hash_match.set_subtitle(
                    "Code SHA-256 calculé ci-dessous." if self._simple_mode else "Empreintes calculées (aucune référence saisie)."
                )
                self._badge_hash.set_label("CALCULÉ")
                self._badge_hash.add_css_class("bf-pill-ok")

        self._proc.run_hash_check(fpath, expected, self._on_telemetry, _done)

    def _run_benchmark(self, *_args: Any) -> None:
        dev = self._get_device()
        if dev:
            self._on_start_job({"action": "usb_benchmark", "device": dev["path"]}, False)

    def _run_qemu(self, *_args: Any) -> None:
        dev = self._get_device()
        if not dev:
            return
        ok, msg = launch_qemu_preview(dev["path"])
        self._on_log("info" if ok else "warn", msg)
        self._on_toast(msg)

    def _run_badblocks(self, *_args: Any) -> None:
        dev = self._get_device()
        if not dev:
            return
        destructive = False if self._simple_mode else self._sw_destructive_bb.get_active()
        job = {"action": "usb_badblocks", "device": dev["path"], "destructive": destructive}
        if destructive:
            present_confirm_destroy_dialog(
                self,
                dev,
                "Test destructif badblocks (-w)",
                "Le mode écriture + lecture efface tous les secteurs pour tester la capacité réelle.",
                lambda: self._on_start_job(job, False),
                simple_mode=self._simple_mode,
            )
        else:
            self._on_start_job(job, False)

    def _run_backup(self, *_args: Any) -> None:
        dev = self._get_device()
        if not dev:
            return
        dlg = Gtk.FileDialog(title="Enregistrer la sauvegarde USB")
        dlg.set_initial_name(f"backup-{dev['name']}.img.gz")
        win = self.get_root()
        assert isinstance(win, Gtk.Window)

        def _done(source: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                f = source.save_finish(res)
                if f and f.get_path():
                    out_p = f.get_path() or ""
                    self._on_start_job(
                        {
                            "action": "usb_backup_img",
                            "device": dev["path"],
                            "output_path": out_p,
                            "compress_gz": out_p.endswith(".gz"),
                        },
                        False,
                    )
            except Exception:
                pass

        dlg.save(win, None, _done)

    def _run_wipe(self, *_args: Any) -> None:
        dev = self._get_device()
        if not dev:
            return
        if self._simple_mode:
            scheme = "gpt"
            fs = "exfat"
            label = "USB_CLEAN"
            op_label = "Réparation complète de la clé USB"
            detail = "Supprime toutes les partitions cachées et remet la clé à sa taille normale."
        else:
            s_idx = self._combo_wipe_scheme.get_selected()
            f_idx = self._combo_wipe_fs.get_selected()
            scheme = self._wipe_schemes[s_idx] if 0 <= s_idx < len(self._wipe_schemes) else "gpt"
            fs = self._wipe_fs[f_idx] if 0 <= f_idx < len(self._wipe_fs) else "exfat"
            label = sanitize_volume_label(self._entry_wipe_label.get_text(), fs)
            op_label = f"Remise à zéro complète ({scheme.upper()} · {fs.upper()})"
            detail = "Supprime toutes les partitions existantes et formate le disque complet."

        job = {
            "action": "usb_wipe_reset",
            "device": dev["path"],
            "scheme": scheme,
            "filesystem": fs,
            "volume_label": label,
        }
        present_confirm_destroy_dialog(
            self,
            dev,
            op_label,
            detail,
            lambda: self._on_start_job(job, False),
            simple_mode=self._simple_mode,
        )
