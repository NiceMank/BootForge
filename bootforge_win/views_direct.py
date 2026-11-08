"""Direct Flash (Rufus-compatible) view for BootForge Windows PyQt6 UI."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bootforge_win.backend import AsyncHelper
from bootforge_win.dialogs import ConfirmDestroyDialog, WinTweaksDialog
from bootforge_win.theme import PartitionMapWidget, format_bytes
from engine.disk_utils import plan_partition_layout, sanitize_volume_label
from engine.iso_inspector import inspect_image


class DirectFlashWinView(QWidget):
    """View adapting between Simple 1-2-3 and Advanced 2-column Rufus Flash Direct."""

    def __init__(
        self,
        get_device: Callable[[], Optional[Dict[str, Any]]],
        is_busy: Callable[[], bool],
        on_start_job: Callable[[Dict[str, Any], bool], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        on_layout_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_device = get_device
        self._is_busy = is_busy
        self._on_start_job = on_start_job
        self._on_log = on_log
        self._on_toast = on_toast
        self._on_layout_changed = on_layout_changed
        self._async_helper = AsyncHelper(self)

        self._simple_mode = True
        self._iso_path = ""
        self._iso_info: Optional[Dict[str, Any]] = None
        self._driver_dir = ""
        self._syncing = False

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

        self.setAcceptDrops(True)
        self._build_ui()
        self.set_simple_mode(True)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._scroll_widget = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_widget)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(14)

        # Columns container (switches between Vertical in Simple and Horizontal in Advanced)
        self._cols_container = QWidget()
        self._cols_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self._cols_container)
        self._cols_layout.setContentsMargins(0, 0, 0, 0)
        self._cols_layout.setSpacing(14)

        # --- LEFT COLUMN (Image source & drivers) ---
        self._left_col = QWidget()
        l_lay = QVBoxLayout(self._left_col)
        l_lay.setContentsMargins(0, 0, 0, 0)
        l_lay.setSpacing(12)

        # Card 1: ISO Source
        self._card_iso = QFrame()
        self._card_iso.setObjectName("Card")
        ci_lay = QVBoxLayout(self._card_iso)
        ci_lay.setContentsMargins(14, 14, 14, 14)
        ci_lay.setSpacing(10)

        self._lbl_iso_title = QLabel("1. Choisir le système à mettre sur la clé")
        self._lbl_iso_title.setObjectName("GroupTitle")
        ci_lay.addWidget(self._lbl_iso_title)

        self._lbl_iso_desc = QLabel("Sélectionnez le fichier .iso ou .img que vous avez téléchargé.")
        self._lbl_iso_desc.setObjectName("GroupSub")
        ci_lay.addWidget(self._lbl_iso_desc)

        # Drop Zone
        self._drop_zone = QFrame()
        self._drop_zone.setObjectName("DropZone")
        dz_lay = QHBoxLayout(self._drop_zone)
        dz_lay.setContentsMargins(14, 12, 14, 12)
        dz_lay.setSpacing(12)

        lbl_icon = QLabel("💿")
        f_i = QFont()
        f_i.setPointSize(18)
        lbl_icon.setFont(f_i)
        dz_lay.addWidget(lbl_icon)

        self._lbl_drop = QLabel("Glissez un fichier .iso ou .img ici, ou cliquez sur Choisir")
        self._lbl_drop.setWordWrap(True)
        dz_lay.addWidget(self._lbl_drop, 1)

        btn_browse = QPushButton("Choisir...")
        btn_browse.setObjectName("PrimaryBtn")
        btn_browse.clicked.connect(self._browse_iso)
        dz_lay.addWidget(btn_browse)
        ci_lay.addWidget(self._drop_zone)

        # Advanced Path input row
        self._iso_path_row = QWidget()
        ip_lay = QHBoxLayout(self._iso_path_row)
        ip_lay.setContentsMargins(0, 0, 0, 0)
        ip_lay.setSpacing(8)
        self._txt_iso_path = QLineEdit()
        self._txt_iso_path.setPlaceholderText("Chemin complet du fichier ISO...")
        self._txt_iso_path.textChanged.connect(self._on_iso_text_changed)
        ip_lay.addWidget(self._txt_iso_path, 1)
        btn_analyze = QPushButton("Analyser")
        btn_analyze.clicked.connect(lambda: self.inspect_iso_path(self._txt_iso_path.text().strip()))
        ip_lay.addWidget(btn_analyze)
        ci_lay.addWidget(self._iso_path_row)

        # Detected summary row
        self._row_detected = QFrame()
        self._row_detected.setObjectName("Card")
        rd_lay = QHBoxLayout(self._row_detected)
        rd_lay.setContentsMargins(10, 8, 10, 8)
        rd_lay.setSpacing(8)
        self._lbl_detected_name = QLabel("Aucun fichier choisi pour l'instant")
        self._lbl_detected_name.setObjectName("Muted")
        rd_lay.addWidget(self._lbl_detected_name, 1)
        self._lbl_detected_badge = QLabel("En attente")
        self._lbl_detected_badge.setObjectName("MonoLabel")
        rd_lay.addWidget(self._lbl_detected_badge)
        ci_lay.addWidget(self._row_detected)

        self._lbl_boot_note = QLabel("Amorce UEFI / BIOS détectée automatiquement")
        self._lbl_boot_note.setObjectName("Muted")
        ci_lay.addWidget(self._lbl_boot_note)
        l_lay.addWidget(self._card_iso)

        # Card: Extra drivers ($WinPEDriver$) - Advanced only
        self._card_drv = QFrame()
        self._card_drv.setObjectName("Card")
        cd_lay = QVBoxLayout(self._card_drv)
        cd_lay.setContentsMargins(14, 14, 14, 14)
        cd_lay.setSpacing(8)
        lbl_drv_title = QLabel("Pilotes Windows supplémentaires ($WinPEDriver$)")
        lbl_drv_title.setObjectName("GroupTitle")
        cd_lay.addWidget(lbl_drv_title)
        cd_lay.addWidget(QLabel("Optionnel : injecte des pilotes NVMe / RAID / Wi-Fi dans le programme d'installation."))
        h_drv = QHBoxLayout()
        self._lbl_drv_path = QLabel("Aucun dossier sélectionné")
        self._lbl_drv_path.setObjectName("Muted")
        h_drv.addWidget(self._lbl_drv_path, 1)
        btn_pick_drv = QPushButton("Parcourir...")
        btn_pick_drv.clicked.connect(self._browse_drivers)
        h_drv.addWidget(btn_pick_drv)
        cd_lay.addLayout(h_drv)
        l_lay.addWidget(self._card_drv)
        l_lay.addStretch()

        self._cols_layout.addWidget(self._left_col)

        # --- RIGHT COLUMN (Format options & tweaks) ---
        self._right_col = QWidget()
        r_lay = QVBoxLayout(self._right_col)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(12)

        # Card: Formatting settings
        self._card_fmt = QFrame()
        self._card_fmt.setObjectName("Card")
        cf_lay = QVBoxLayout(self._card_fmt)
        cf_lay.setContentsMargins(14, 14, 14, 14)
        cf_lay.setSpacing(10)

        self._lbl_fmt_title = QLabel("2. Réglages de la clé")
        self._lbl_fmt_title.setObjectName("GroupTitle")
        cf_lay.addWidget(self._lbl_fmt_title)

        self._lbl_fmt_desc = QLabel("Les options techniques sont choisies automatiquement selon votre fichier.")
        self._lbl_fmt_desc.setObjectName("GroupSub")
        cf_lay.addWidget(self._lbl_fmt_desc)

        # Mode d'écriture (ISO / DD) - Advanced only
        self._row_write_mode = QWidget()
        wm_lay = QHBoxLayout(self._row_write_mode)
        wm_lay.setContentsMargins(0, 0, 0, 0)
        wm_lay.addWidget(QLabel("Mode d'écriture :"))
        self._cmb_write = QComboBox()
        self._write_modes = ["iso", "dd"]
        self._cmb_write.addItems(["ISO (Extraction Rufus)", "DD (Copie bloc par bloc)"])
        self._cmb_write.currentIndexChanged.connect(lambda: self._sync_ui_state())
        wm_lay.addWidget(self._cmb_write, 1)
        cf_lay.addWidget(self._row_write_mode)

        # Schéma de partition (GPT / MBR) - Advanced only
        self._row_scheme = QWidget()
        sc_lay = QHBoxLayout(self._row_scheme)
        sc_lay.setContentsMargins(0, 0, 0, 0)
        sc_lay.addWidget(QLabel("Schéma de partition :"))
        self._cmb_scheme = QComboBox()
        self._schemes = ["gpt", "mbr"]
        self._cmb_scheme.addItems(["GPT (UEFI moderne)", "MBR (BIOS hérité / UEFI-CSM)"])
        self._cmb_scheme.currentIndexChanged.connect(lambda: self._emit_layout_change())
        sc_lay.addWidget(self._cmb_scheme, 1)
        cf_lay.addWidget(self._row_scheme)

        # Système de fichiers (NTFS / FAT32 / exFAT) - Advanced only
        self._row_fs = QWidget()
        fs_lay = QHBoxLayout(self._row_fs)
        fs_lay.setContentsMargins(0, 0, 0, 0)
        fs_lay.addWidget(QLabel("Système de fichiers :"))
        self._cmb_fs = QComboBox()
        self._filesystems = ["ntfs", "fat32", "exfat"]
        self._cmb_fs.addItems(["NTFS (+ UEFI:NTFS)", "FAT32 (WIM split automatique)", "exFAT (+ UEFI:NTFS)"])
        self._cmb_fs.currentIndexChanged.connect(lambda: self._sync_ui_state())
        fs_lay.addWidget(self._cmb_fs, 1)
        cf_lay.addWidget(self._row_fs)

        # Volume Label
        h_lbl = QHBoxLayout()
        self._lbl_name_tag = QLabel("Nom de la clé :")
        h_lbl.addWidget(self._lbl_name_tag)
        self._txt_label = QLineEdit("BOOTFORGE")
        self._txt_label.textChanged.connect(self._update_label_preview)
        h_lbl.addWidget(self._txt_label, 1)
        cf_lay.addLayout(h_lbl)

        # Quick format checkbox
        self._chk_quick = QCheckBox("Formatage rapide")
        self._chk_quick.setChecked(True)
        cf_lay.addWidget(self._chk_quick)
        r_lay.addWidget(self._card_fmt)

        # Card: Windows Tweaks Card
        self._card_win = QFrame()
        self._card_win.setObjectName("Card")
        cw_lay = QVBoxLayout(self._card_win)
        cw_lay.setContentsMargins(14, 14, 14, 14)
        cw_lay.setSpacing(8)

        lbl_w_title = QLabel("Options Windows 11")
        lbl_w_title.setObjectName("GroupTitle")
        cw_lay.addWidget(lbl_w_title)

        self._chk_win_simple = QCheckBox("Installer Windows 11 sans blocage matériel (TPM, Secure Boot, compte en ligne)")
        self._chk_win_simple.setChecked(True)
        self._chk_win_simple.toggled.connect(self._on_simple_win_toggled)
        cw_lay.addWidget(self._chk_win_simple)

        # Advanced XML Button row
        self._row_win_adv = QWidget()
        wa_lay = QHBoxLayout(self._row_win_adv)
        wa_lay.setContentsMargins(0, 0, 0, 0)
        self._lbl_win_count = QLabel("6 options actives (autounattend.xml)")
        wa_lay.addWidget(self._lbl_win_count, 1)
        btn_cfg_win = QPushButton("Personnaliser XML...")
        btn_cfg_win.clicked.connect(self._open_win_tweaks_dialog)
        wa_lay.addWidget(btn_cfg_win)
        cw_lay.addWidget(self._row_win_adv)

        self._lbl_win_tip = QLabel("💡 Astuce : débranchez vos autres disques externes pendant l'installation Windows.")
        self._lbl_win_tip.setObjectName("Muted")
        cw_lay.addWidget(self._lbl_win_tip)
        r_lay.addWidget(self._card_win)

        # Card: Linux Persistence (Advanced / when Linux)
        self._card_persist = QFrame()
        self._card_persist.setObjectName("Card")
        cp_lay = QVBoxLayout(self._card_persist)
        cp_lay.setContentsMargins(14, 14, 14, 14)
        cp_lay.setSpacing(8)

        lbl_p_title = QLabel("Conserver vos données personnelles (Linux Live)")
        lbl_p_title.setObjectName("GroupTitle")
        cp_lay.addWidget(lbl_p_title)

        h_sp = QHBoxLayout()
        h_sp.addWidget(QLabel("Espace de sauvegarde persistant (Go) :"))
        self._spin_persist = QSpinBox()
        self._spin_persist.setRange(0, 64)
        self._spin_persist.setValue(0)
        self._spin_persist.valueChanged.connect(lambda: self._emit_layout_change())
        h_sp.addWidget(self._spin_persist)
        cp_lay.addLayout(h_sp)
        r_lay.addWidget(self._card_persist)
        r_lay.addStretch()

        self._cols_layout.addWidget(self._right_col)
        self._scroll_layout.addWidget(self._cols_container)

        # Partition Map preview (Advanced only)
        self._part_card = QFrame()
        self._part_card.setObjectName("Card")
        p_card_lay = QVBoxLayout(self._part_card)
        p_card_lay.setContentsMargins(14, 12, 14, 12)
        self._part_map = PartitionMapWidget()
        p_card_lay.addWidget(self._part_map)
        self._scroll_layout.addWidget(self._part_card)

        scroll.setWidget(self._scroll_widget)
        main_layout.addWidget(scroll, 1)

        # Action footer
        h_foot = QHBoxLayout()
        h_foot.setSpacing(10)
        self._lbl_action_hint = QLabel()
        self._lbl_action_hint.setObjectName("Muted")
        h_foot.addWidget(self._lbl_action_hint, 1)

        self._btn_dry = QPushButton("Simuler (dry-run)")
        self._btn_dry.clicked.connect(lambda: self._trigger_flash(dry_run=True))
        h_foot.addWidget(self._btn_dry)

        self._btn_flash = QPushButton("Créer la clé démarrable")
        self._btn_flash.setObjectName("PrimaryBtn")
        self._btn_flash.clicked.connect(lambda: self._trigger_flash(dry_run=False))
        h_foot.addWidget(self._btn_flash)

        main_layout.addLayout(h_foot)

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple

        if simple:
            self._cols_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self._cols_layout.setSpacing(14)
            self._lbl_iso_title.setText("1. Choisir le système à mettre sur la clé")
            self._lbl_iso_desc.setText("Sélectionnez le fichier .iso ou .img que vous avez téléchargé.")
            self._lbl_fmt_title.setText("2. Réglages de la clé")
            self._lbl_fmt_desc.setText("Les options techniques sont choisies automatiquement selon votre fichier.")
            self._lbl_name_tag.setText("Nom de la clé (facultatif) :")
            self._btn_flash.setText("Créer la clé démarrable")
        else:
            self._cols_layout.setDirection(QBoxLayout.Direction.LeftToRight)
            self._cols_layout.setSpacing(16)
            self._lbl_iso_title.setText("Image source (ISO / IMG / RAW)")
            self._lbl_iso_desc.setText("Inspection directe sans montage physique pour adapter le schéma.")
            self._lbl_fmt_title.setText("Paramètres de formatage")
            self._lbl_fmt_desc.setText("Schéma GPT/MBR, chargeur UEFI:NTFS, découpe WIM ou écriture bloc DD.")
            self._lbl_name_tag.setText("Nom du volume :")
            self._btn_flash.setText("Démarrer le flash")

        # Toggle visibility of advanced controls
        self._iso_path_row.setVisible(not simple)
        self._card_drv.setVisible(not simple)
        self._row_write_mode.setVisible(not simple)
        self._row_scheme.setVisible(not simple)
        self._row_fs.setVisible(not simple)
        self._chk_quick.setVisible(not simple)

        self._chk_win_simple.setVisible(simple)
        self._row_win_adv.setVisible(not simple)
        self._part_card.setVisible(not simple)
        self._btn_dry.setVisible(not simple)

        self._sync_ui_state()
        self.sync_sensitivity()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            fpath = url.toLocalFile()
            if fpath and os.path.isfile(fpath):
                self.load_iso_external(fpath)
                event.acceptProposedAction()
                break

    def load_iso_external(self, path: str) -> None:
        self._txt_iso_path.setText(path)
        self.inspect_iso_path(path)

    def _browse_iso(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner un fichier système (.iso, .img)",
            "",
            "Images système (*.iso *.ISO *.img *.IMG *.raw *.vhd *.vhdx *.gz *.xz);;Tous les fichiers (*.*)",
        )
        if fn:
            self.load_iso_external(fn)

    def _browse_drivers(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choisir le dossier de pilotes ($WinPEDriver$)")
        if d:
            self._driver_dir = d
            self._lbl_drv_path.setText(d)

    def _on_iso_text_changed(self, text: str) -> None:
        self._iso_path = text.strip()
        self.sync_sensitivity()

    def inspect_iso_path(self, path: str) -> None:
        if not path or not os.path.isfile(path):
            return
        self._iso_path = path
        self._lbl_detected_name.setText("Analyse du fichier en cours...")
        self._on_log("info", f"Analyse de l'image : {path}")

        def _do() -> Dict[str, Any]:
            return inspect_image(path)

        def _ok(info: Dict[str, Any]) -> None:
            self._iso_info = info
            self._apply_iso_recommendations(info)
            self._on_log("info", f"Image inspectée : {info.get('filename')} ({info.get('os_label')}, {format_bytes(info.get('size_bytes', 0))})")

        def _err(msg: str) -> None:
            self._iso_info = None
            self._lbl_detected_name.setText(f"Impossible de lire ce fichier : {msg}")
            self._lbl_detected_badge.setText("Erreur")
            self._on_toast(f"Erreur image : {msg}")
            self._sync_ui_state()

        self._async_helper.run_fn(_do, _ok, _err)

    def _apply_iso_recommendations(self, info: Dict[str, Any]) -> None:
        self._syncing = True
        try:
            # Recomm write mode
            r_wm = info.get("recommended_write_mode", "iso")
            self._cmb_write.setCurrentIndex(self._write_modes.index(r_wm) if r_wm in self._write_modes else 0)

            # Recomm scheme
            r_sc = info.get("recommended_scheme", "gpt")
            self._cmb_scheme.setCurrentIndex(self._schemes.index(r_sc) if r_sc in self._schemes else 0)

            # Recomm fs
            r_fs = info.get("recommended_fs", "ntfs")
            self._cmb_fs.setCurrentIndex(self._filesystems.index(r_fs) if r_fs in self._filesystems else 0)

            os_fam = info.get("os_family", "unknown")
            if os_fam == "windows":
                self._txt_label.setText("WIN11_USB")
            elif os_fam == "linux":
                self._txt_label.setText("LINUX_LIVE")
            else:
                self._txt_label.setText("BOOTFORGE")

            self._lbl_detected_name.setText(f"{info.get('filename')} · {format_bytes(info.get('size_bytes', 0))}")
            self._lbl_detected_badge.setText(str(info.get("os_label") or os_fam).upper())

            notes = info.get("notes") or []
            wim_sz = int(info.get("install_wim_size") or 0)
            if wim_sz > 0:
                self._lbl_boot_note.setText(f"install.wim : {format_bytes(wim_sz)}" + (f" — {notes[0]}" if notes else ""))
            elif notes:
                self._lbl_boot_note.setText(" · ".join(notes[:2]))
            else:
                self._lbl_boot_note.setText(f"Amorce UEFI : {'Oui' if info.get('has_uefi_boot') else 'Hybride / DD'}")
        finally:
            self._syncing = False

        self._sync_ui_state()

    def _current_write_mode(self) -> str:
        idx = max(0, self._cmb_write.currentIndex())
        return self._write_modes[idx] if idx < len(self._write_modes) else "iso"

    def _current_scheme(self) -> str:
        idx = max(0, self._cmb_scheme.currentIndex())
        return self._schemes[idx] if idx < len(self._schemes) else "gpt"

    def _current_fs(self) -> str:
        idx = max(0, self._cmb_fs.currentIndex())
        return self._filesystems[idx] if idx < len(self._filesystems) else "ntfs"

    def _update_label_preview(self) -> None:
        sanitized = sanitize_volume_label(self._txt_label.text(), self._current_fs())
        if self._txt_label.text() != sanitized and not self._syncing:
            # We don't forcibly override while typing to avoid jerky cursor, but sanitize on read
            pass

    def _sync_ui_state(self) -> None:
        is_iso = self._current_write_mode() == "iso"
        self._cmb_scheme.setEnabled(is_iso)
        self._cmb_fs.setEnabled(is_iso)
        self._txt_label.setEnabled(is_iso)
        self._chk_quick.setEnabled(is_iso)

        os_fam = (self._iso_info or {}).get("os_family", "unknown")
        if self._simple_mode:
            self._card_win.setVisible(is_iso and os_fam == "windows")
            self._card_persist.setVisible(is_iso and os_fam == "linux")
        else:
            self._card_win.setVisible(is_iso and os_fam != "linux")
            self._card_persist.setVisible(is_iso and os_fam != "windows")

        if not self._simple_mode:
            self.refresh_partition_map()
        self.sync_sensitivity()
        if not self._syncing:
            self._on_layout_changed()

    def _emit_layout_change(self) -> None:
        if not self._simple_mode:
            self.refresh_partition_map()
        if not self._syncing:
            self._on_layout_changed()

    def refresh_partition_map(self) -> None:
        dev = self._get_device()
        disk_bytes = int(dev.get("size_bytes", 32 * 1024**3)) if dev else 32 * 1024**3
        write_mode = self._current_write_mode()
        scheme = self._current_scheme()
        fs = self._current_fs()
        os_fam = (self._iso_info or {}).get("os_family", "unknown")
        persist_gb = float(self._spin_persist.value()) if (write_mode == "iso" and os_fam != "windows") else 0.0

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

    def _on_simple_win_toggled(self, checked: bool) -> None:
        self._win_tweaks["enabled"] = checked
        if checked:
            self._win_tweaks["bypass_tpm_secureboot_ram"] = True
            self._win_tweaks["bypass_microsoft_account"] = True
            self._win_tweaks["disable_bitlocker"] = True
            self._win_tweaks["skip_product_key"] = True

    def _open_win_tweaks_dialog(self) -> None:
        def _applied(opts: Dict[str, Any]) -> None:
            self._win_tweaks = opts
            self._chk_win_simple.setChecked(bool(opts.get("enabled", True)))
            self._on_toast("Options Windows (autounattend.xml) mises à jour.")

        dlg = WinTweaksDialog(self._win_tweaks, _applied, self)
        dlg.exec()

    def sync_sensitivity(self) -> None:
        dev = self._get_device()
        busy = self._is_busy()
        iso_ok = bool(self._iso_path and os.path.isfile(self._iso_path))
        dev_ok = bool(dev and not dev.get("is_system_disk") and not dev.get("read_only"))

        can_run = dev_ok and iso_ok and not busy
        self._btn_dry.setEnabled(can_run)
        self._btn_flash.setEnabled(can_run)

        if busy:
            self._lbl_action_hint.setText("Opération en cours, veuillez patienter...")
        elif not dev:
            self._lbl_action_hint.setText("Branchez une clé USB pour commencer." if self._simple_mode else "Sélectionnez une clé USB cible ci-dessus.")
        elif dev.get("is_system_disk"):
            self._lbl_action_hint.setText("Disque système protégé : écriture bloquée.")
        elif not iso_ok:
            self._lbl_action_hint.setText("Sélectionnez un fichier .iso ou .img à l'étape 1.")
        else:
            fname = os.path.basename(self._iso_path)
            if self._simple_mode:
                self._lbl_action_hint.setText(f"Prêt : {fname} sera installé sur {dev.get('model', 'la clé USB')}.")
            else:
                self._lbl_action_hint.setText(f"Prêt : {fname} -> {dev.get('path')} ({format_bytes(dev.get('size_bytes', 0))})")

    def _build_job(self) -> Dict[str, Any]:
        dev = self._get_device()
        assert dev is not None
        os_fam = (self._iso_info or {}).get("os_family", "unknown")
        write_mode = self._current_write_mode()
        fs = self._current_fs()

        return {
            "action": "flash_direct",
            "device": dev["path"],
            "image_path": self._iso_path,
            "scheme": self._current_scheme(),
            "filesystem": fs,
            "write_mode": write_mode,
            "volume_label": sanitize_volume_label(self._txt_label.text(), fs),
            "quick_format": True if self._simple_mode else self._chk_quick.isChecked(),
            "persistence_gb": float(self._spin_persist.value()) if (write_mode == "iso" and os_fam != "windows") else 0.0,
            "persistence_type": "casper-rw",
            "driver_dir": "" if self._simple_mode else self._driver_dir,
            "windows_tweaks": self._win_tweaks if (write_mode == "iso" and os_fam == "windows") else {"enabled": False},
        }

    def _trigger_flash(self, dry_run: bool) -> None:
        dev = self._get_device()
        if not dev or dev.get("is_system_disk"):
            return
        if not self._iso_path or not os.path.isfile(self._iso_path):
            self._on_toast("Fichier image introuvable.")
            return

        job = self._build_job()
        if dry_run:
            self._on_start_job(job, True)
            return

        op_title = "Création de clé USB démarrable" if self._simple_mode else f"Flash direct ({job['write_mode'].upper()} · {job['scheme'].upper()} · {job['filesystem'].upper()})"
        dlg = ConfirmDestroyDialog(
            device=dev,
            operation_title=op_title,
            detail_msg=f"Fichier à installer : {os.path.basename(self._iso_path)}",
            on_confirmed=lambda: self._on_start_job(job, False),
            simple_mode=self._simple_mode,
            parent=self,
        )
        dlg.exec()
