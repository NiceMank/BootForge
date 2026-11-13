"""USB diagnostics, integrity checks, and factory reset view for BootForge Windows PyQt6 UI."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bootforge_win.backend import WinTaskController
from bootforge_win.dialogs import ConfirmDestroyDialog
from bootforge_win.theme import format_bytes, format_speed
from engine.disk_utils import sanitize_volume_label
from engine.i18n import get_current_language, t


class ToolboxWinView(QWidget):
    """USB repair, file hash check, speed benchmark, and factory reset tools."""

    def __init__(
        self,
        get_device: Callable[[], Optional[Dict[str, Any]]],
        task_ctrl: WinTaskController,
        on_start_job: Callable[[Dict[str, Any], bool], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_device = get_device
        self._ctrl = task_ctrl
        self._on_start_job = on_start_job
        self._on_log = on_log
        self._on_toast = on_toast
        self._simple_mode = True

        self._build_ui()
        self.set_simple_mode(True)
        self._ctrl.hash_done.connect(self._on_hash_done)

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

        # --- Card 1: File integrity hash verifier ---
        self._card_hash = QFrame()
        self._card_hash.setObjectName("Card")
        ch_lay = QVBoxLayout(self._card_hash)
        ch_lay.setContentsMargins(14, 14, 14, 14)
        ch_lay.setSpacing(10)

        self._lbl_hash_title = QLabel("Vérifier qu'un fichier téléchargé n'est pas abîmé")
        self._lbl_hash_title.setObjectName("GroupTitle")
        ch_lay.addWidget(self._lbl_hash_title)

        self._lbl_hash_desc = QLabel("Optionnel : compare votre fichier ISO avec le code officiel fourni sur le site.")
        self._lbl_hash_desc.setObjectName("GroupSub")
        ch_lay.addWidget(self._lbl_hash_desc)

        h_f = QHBoxLayout()
        self._txt_hash_file = QLineEdit()
        self._txt_hash_file.setPlaceholderText(t("toolbox_hash_file_ph"))
        h_f.addWidget(self._txt_hash_file, 1)
        self._btn_pick_hf = QPushButton(t("toolbox_btn_browse"))
        self._btn_pick_hf.clicked.connect(self._browse_hash_file)
        h_f.addWidget(self._btn_pick_hf)
        ch_lay.addLayout(h_f)

        self._txt_expected_hash = QLineEdit()
        self._txt_expected_hash.setPlaceholderText(t("toolbox_hash_expected_ph"))
        ch_lay.addWidget(self._txt_expected_hash)

        h_act = QHBoxLayout()
        self._btn_calc_hash = QPushButton(t("toolbox_hash_btn_simple"))
        self._btn_calc_hash.setObjectName("PrimaryBtn")
        self._btn_calc_hash.clicked.connect(self._run_hash_check)
        h_act.addWidget(self._btn_calc_hash)
        h_act.addStretch()

        self._lbl_hash_result = QLabel()
        self._lbl_hash_result.setObjectName("MonoLabel")
        h_act.addWidget(self._lbl_hash_result)
        ch_lay.addLayout(h_act)

        # Hash result labels
        self._row_sha256 = QWidget()
        s256_lay = QHBoxLayout(self._row_sha256)
        s256_lay.setContentsMargins(0, 0, 0, 0)
        s256_lay.addWidget(QLabel("<b>SHA-256 :</b>"))
        self._txt_sha256 = QLineEdit()
        self._txt_sha256.setReadOnly(True)
        s256_lay.addWidget(self._txt_sha256, 1)
        self._btn_cp_sha = QPushButton(t("toolbox_btn_copy"))
        self._btn_cp_sha.clicked.connect(lambda: self._copy_text(self._txt_sha256.text()))
        s256_lay.addWidget(self._btn_cp_sha)
        ch_lay.addWidget(self._row_sha256)
        self._row_sha256.setVisible(False)

        self._scroll_layout.addWidget(self._card_hash)

        # --- Card 2: Speed Benchmark ---
        self._card_bench = QFrame()
        self._card_bench.setObjectName("Card")
        cb_lay = QVBoxLayout(self._card_bench)
        cb_lay.setContentsMargins(14, 14, 14, 14)
        cb_lay.setSpacing(8)

        self._lbl_bench_title = QLabel("Vitesse de la clé USB")
        self._lbl_bench_title.setObjectName("GroupTitle")
        cb_lay.addWidget(self._lbl_bench_title)

        self._lbl_bench_desc = QLabel("Mesure le débit de lecture réel de votre clé sur ce port USB (échantillon 256 Mo).")
        self._lbl_bench_desc.setObjectName("GroupSub")
        cb_lay.addWidget(self._lbl_bench_desc)

        h_bact = QHBoxLayout()
        self._btn_bench = QPushButton("Mesurer la vitesse")
        self._btn_bench.clicked.connect(self._run_benchmark)
        h_bact.addWidget(self._btn_bench)

        self._lbl_bench_result = QLabel("—")
        self._lbl_bench_result.setObjectName("MonoLabel")
        h_bact.addWidget(self._lbl_bench_result)
        h_bact.addStretch()
        cb_lay.addLayout(h_bact)
        self._scroll_layout.addWidget(self._card_bench)

        # --- Card 3: USB Health / Badblocks ---
        self._card_bb = QFrame()
        self._card_bb.setObjectName("Card")
        cbb_lay = QVBoxLayout(self._card_bb)
        cbb_lay.setContentsMargins(14, 14, 14, 14)
        cbb_lay.setSpacing(8)

        self._lbl_bb_title = QLabel("Vérifier l'état de la clé USB")
        self._lbl_bb_title.setObjectName("GroupTitle")
        cbb_lay.addWidget(self._lbl_bb_title)

        self._lbl_bb_desc = QLabel("Recherche d'éventuels secteurs défectueux ou fausses capacités matérielles.")
        self._lbl_bb_desc.setObjectName("GroupSub")
        cbb_lay.addWidget(self._lbl_bb_desc)

        self._chk_destructive_bb = QCheckBox("Test complet en écriture + lecture (efface tout pour tester la capacité réelle)")
        self._chk_destructive_bb.setChecked(False)
        cbb_lay.addWidget(self._chk_destructive_bb)

        h_bbact = QHBoxLayout()
        self._btn_badblocks = QPushButton("Tester la fiabilité")
        self._btn_badblocks.clicked.connect(self._run_badblocks)
        h_bbact.addWidget(self._btn_badblocks)

        self._lbl_bb_result = QLabel()
        h_bbact.addWidget(self._lbl_bb_result)
        h_bbact.addStretch()
        cbb_lay.addLayout(h_bbact)
        self._scroll_layout.addWidget(self._card_bb)

        # --- Card 4: Factory Reset / Wipe ---
        self._card_wipe = QFrame()
        self._card_wipe.setObjectName("Card")
        cw_lay = QVBoxLayout(self._card_wipe)
        cw_lay.setContentsMargins(14, 14, 14, 14)
        cw_lay.setSpacing(10)

        self._lbl_wipe_title = QLabel("Réparer une clé USB (tout effacer)")
        self._lbl_wipe_title.setObjectName("GroupTitle")
        cw_lay.addWidget(self._lbl_wipe_title)

        self._lbl_wipe_desc = QLabel("Si votre clé affiche une capacité réduite ou est illisible, ce nettoyage restaure toute sa taille d'origine.")
        self._lbl_wipe_desc.setObjectName("GroupSub")
        cw_lay.addWidget(self._lbl_wipe_desc)

        # Advanced options for wipe
        self._row_wipe_adv = QWidget()
        wa_lay = QHBoxLayout(self._row_wipe_adv)
        wa_lay.setContentsMargins(0, 0, 0, 0)

        self._lbl_wipe_tbl = QLabel(t("toolbox_wipe_lbl_table"))
        wa_lay.addWidget(self._lbl_wipe_tbl)
        self._cmb_wipe_scheme = QComboBox()
        self._wipe_schemes = ["gpt", "mbr"]
        self._cmb_wipe_scheme.addItems(["GPT (Moderne)", "MBR (Hérité)"])
        wa_lay.addWidget(self._cmb_wipe_scheme)

        self._lbl_wipe_fsl = QLabel(t("toolbox_wipe_lbl_format"))
        wa_lay.addWidget(self._lbl_wipe_fsl)
        self._cmb_wipe_fs = QComboBox()
        self._wipe_filesystems = ["exfat", "fat32", "ntfs"]
        self._cmb_wipe_fs.addItems(["exFAT (Universel)", "FAT32 (Classique)", "NTFS (Windows)"])
        wa_lay.addWidget(self._cmb_wipe_fs)

        self._lbl_wipe_nml = QLabel(t("toolbox_wipe_lbl_name"))
        wa_lay.addWidget(self._lbl_wipe_nml)
        self._txt_wipe_label = QLineEdit("USB_CLEAN")
        wa_lay.addWidget(self._txt_wipe_label)
        cw_lay.addWidget(self._row_wipe_adv)

        h_wact = QHBoxLayout()
        self._btn_wipe = QPushButton(t("toolbox_wipe_btn_simple"))
        self._btn_wipe.setObjectName("DangerBtn")
        self._btn_wipe.clicked.connect(self._run_wipe)
        h_wact.addWidget(self._btn_wipe)
        h_wact.addStretch()
        cw_lay.addLayout(h_wact)
        self._scroll_layout.addWidget(self._card_wipe)

        # --- Card 5: Disk Clone / Backup (.img) - Advanced only ---
        self._card_backup = QFrame()
        self._card_backup.setObjectName("Card")
        cbk_lay = QVBoxLayout(self._card_backup)
        cbk_lay.setContentsMargins(14, 14, 14, 14)
        cbk_lay.setSpacing(8)

        self._lbl_bk_title = QLabel(t("toolbox_backup_title"))
        self._lbl_bk_title.setObjectName("GroupTitle")
        cbk_lay.addWidget(self._lbl_bk_title)
        self._lbl_bk_desc = QLabel(t("toolbox_backup_desc"))
        cbk_lay.addWidget(self._lbl_bk_desc)

        h_bk = QHBoxLayout()
        self._btn_backup = QPushButton(t("toolbox_backup_btn"))
        self._btn_backup.clicked.connect(self._run_backup)
        h_bk.addWidget(self._btn_backup)
        self._btn_restore = QPushButton(t("toolbox_clone_btn_restore"))
        self._btn_restore.setObjectName("DangerBtn")
        self._btn_restore.clicked.connect(self._run_restore)
        h_bk.addWidget(self._btn_restore)
        h_bk.addStretch()
        cbk_lay.addLayout(h_bk)
        self._scroll_layout.addWidget(self._card_backup)

        # --- Card 6: QEMU Virtual Boot - Advanced only ---
        self._card_qemu = QFrame()
        self._card_qemu.setObjectName("Card")
        cq_lay = QVBoxLayout(self._card_qemu)
        cq_lay.setContentsMargins(14, 14, 14, 14)
        cq_lay.setSpacing(8)

        self._lbl_q_title = QLabel(t("toolbox_qemu_title"))
        self._lbl_q_title.setObjectName("GroupTitle")
        cq_lay.addWidget(self._lbl_q_title)
        self._lbl_q_desc = QLabel(t("toolbox_qemu_desc"))
        cq_lay.addWidget(self._lbl_q_desc)

        h_q = QHBoxLayout()
        self._btn_qemu = QPushButton(t("toolbox_qemu_btn"))
        self._btn_qemu.clicked.connect(self._run_qemu)
        h_q.addWidget(self._btn_qemu)
        h_q.addStretch()
        cq_lay.addLayout(h_q)
        self._scroll_layout.addWidget(self._card_qemu)

        self._scroll_layout.addStretch()
        scroll.setWidget(self._scroll_widget)
        main_layout.addWidget(scroll)

    def apply_language(self) -> None:
        """Refresh all visible labels and buttons to match current language."""
        self._txt_hash_file.setPlaceholderText(t("toolbox_hash_file_ph"))
        self._txt_expected_hash.setPlaceholderText(t("toolbox_hash_expected_ph"))
        self._btn_pick_hf.setText(t("toolbox_btn_browse"))
        self._btn_cp_sha.setText(t("toolbox_btn_copy"))
        self._chk_destructive_bb.setText(t("toolbox_bb_destructive_chk"))
        self._lbl_wipe_tbl.setText(t("toolbox_wipe_lbl_table"))
        self._lbl_wipe_fsl.setText(t("toolbox_wipe_lbl_format"))
        self._lbl_wipe_nml.setText(t("toolbox_wipe_lbl_name"))
        self._lbl_bk_title.setText(t("toolbox_backup_title"))
        self._lbl_bk_desc.setText(t("toolbox_backup_desc"))
        self._btn_backup.setText(t("toolbox_backup_btn"))
        self._btn_restore.setText(t("toolbox_clone_btn_restore"))
        self._lbl_q_title.setText(t("toolbox_qemu_title"))
        self._lbl_q_desc.setText(t("toolbox_qemu_desc"))
        self._btn_qemu.setText(t("toolbox_qemu_btn"))

        if self._simple_mode:
            self._lbl_hash_title.setText(t("toolbox_hash_title_simple"))
            self._lbl_hash_desc.setText(t("toolbox_hash_desc_simple"))
            self._btn_calc_hash.setText(t("toolbox_hash_btn_simple"))
            self._lbl_bench_title.setText(t("toolbox_bench_title_simple"))
            self._lbl_bench_desc.setText(t("toolbox_bench_desc_simple"))
            self._btn_bench.setText(t("toolbox_bench_btn_simple"))
            self._lbl_bb_title.setText(t("toolbox_bb_title_simple"))
            self._lbl_bb_desc.setText(t("toolbox_bb_desc_simple"))
            self._btn_badblocks.setText(t("toolbox_bb_btn_simple"))
            self._lbl_wipe_title.setText(t("toolbox_wipe_title_simple"))
            self._lbl_wipe_desc.setText(t("toolbox_wipe_desc_simple"))
            self._btn_wipe.setText(t("toolbox_wipe_btn_simple"))
        else:
            self._lbl_hash_title.setText(t("toolbox_hash_title_adv"))
            self._lbl_hash_desc.setText(t("toolbox_hash_desc_adv"))
            self._btn_calc_hash.setText(t("toolbox_hash_btn_adv"))
            self._lbl_bench_title.setText(t("toolbox_bench_title_adv"))
            self._lbl_bench_desc.setText(t("toolbox_bench_desc_adv"))
            self._btn_bench.setText(t("toolbox_bench_btn_adv"))
            self._lbl_bb_title.setText(t("toolbox_bb_title_adv"))
            self._lbl_bb_desc.setText(t("toolbox_bb_desc_adv"))
            self._btn_badblocks.setText(t("toolbox_bb_btn_adv"))
            self._lbl_wipe_title.setText(t("toolbox_wipe_title_adv"))
            self._lbl_wipe_desc.setText(t("toolbox_wipe_desc_adv"))
            self._btn_wipe.setText(t("toolbox_wipe_btn_adv"))

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple
        self.apply_language()

        self._chk_destructive_bb.setVisible(not simple)
        self._row_wipe_adv.setVisible(not simple)
        self._card_backup.setVisible(not simple)
        self._card_qemu.setVisible(not simple)
        self.sync_sensitivity()

    def set_benchmark_result(self, mbps: float) -> None:
        self._lbl_bench_result.setText(f"{mbps:.1f} Mo/s")

    def _copy_text(self, text: str) -> None:
        if text:
            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(text)
            self._on_toast("Empreinte copiée dans le presse-papiers.")

    def _browse_hash_file(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Choisir un fichier à vérifier", "", "Tous les fichiers (*.*)")
        if fn:
            self._txt_hash_file.setText(fn)

    def _run_hash_check(self) -> None:
        path = self._txt_hash_file.text().strip()
        if not path or not os.path.isfile(path):
            self._on_toast("Fichier introuvable.")
            return

        expected = self._txt_expected_hash.text().strip()
        self._lbl_hash_result.setText("Calcul en cours...")
        self._ctrl.run_hash_check(path, expected)

    def _on_hash_done(self, res: Any, err: str) -> None:
        if err or not res:
            self._lbl_hash_result.setText(f"Erreur : {err}")
            self._on_toast(f"Erreur calcul : {err}")
            return

        hashes = res.get("hashes") or {}
        sha256 = hashes.get("sha256", "")
        self._txt_sha256.setText(sha256)
        self._row_sha256.setVisible(True)

        matched = res.get("matched")
        if matched is True:
            self._lbl_hash_result.setText("✅ Fichier intact et conforme.")
            self._on_toast("Fichier vérifié : conforme au code officiel !")
        elif matched is False:
            self._lbl_hash_result.setText("❌ Attention : empreinte différente !")
            self._on_toast("Attention : le fichier ne correspond pas au code fourni !")
        else:
            self._lbl_hash_result.setText("Calcul terminé.")

    def _run_benchmark(self) -> None:
        dev = self._get_device()
        if not dev:
            return
        self._lbl_bench_result.setText("Mesure en cours...")
        self._on_start_job({"action": "usb_benchmark", "device": dev["path"]}, False)

    def _run_badblocks(self) -> None:
        dev = self._get_device()
        if not dev:
            return
        destructive = self._chk_destructive_bb.isChecked() if not self._simple_mode else False
        job = {"action": "usb_badblocks", "device": dev["path"], "destructive": destructive}

        if destructive:
            dlg = ConfirmDestroyDialog(
                device=dev,
                operation_title="Test de surface destructif",
                detail_msg="Le mode écriture + lecture efface toutes les données pour tester la mémoire flash réelle.",
                on_confirmed=lambda: self._on_start_job(job, False),
                simple_mode=self._simple_mode,
                parent=self,
            )
            dlg.exec()
        else:
            self._on_start_job(job, False)

    def _run_backup(self) -> None:
        dev = self._get_device()
        if not dev:
            return
        fn, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la sauvegarde de la clé USB",
            f"backup-{dev.get('name', 'usb')}.img.gz",
            "Images disque (*.img.gz *.img);;Tous les fichiers (*.*)",
        )
        if fn:
            self._on_start_job(
                {
                    "action": "usb_backup_img",
                    "device": dev["path"],
                    "output_path": fn,
                    "compress_gz": fn.lower().endswith(".gz"),
                },
                False,
            )

    def _run_restore(self) -> None:
        dev = self._get_device()
        if not dev:
            return
        fn, _ = QFileDialog.getOpenFileName(
            self,
            t("toolbox_clone_restore_opt"),
            "",
            "Images disque (*.img *.img.gz *.img.xz);;Tous les fichiers (*.*)",
        )
        if not fn:
            return
        job = {
            "action": "usb_restore_img",
            "device": dev["path"],
            "image_path": fn,
        }
        dlg = ConfirmDestroyDialog(
            device=dev,
            operation_title=t("toolbox_clone_restore_opt"),
            detail_msg=f"{t('toolbox_clone_desc')} ({os.path.basename(fn)})",
            on_confirmed=lambda: self._on_start_job(job, False),
            simple_mode=self._simple_mode,
            parent=self,
        )
        dlg.exec()

    def _run_qemu(self) -> None:
        dev = self._get_device()
        if not dev:
            return
        qemu_bin = shutil.which("qemu-system-x86_64") or (
            r"C:\Program Files\qemu\qemu-system-x86_64.exe"
            if os.path.isfile(r"C:\Program Files\qemu\qemu-system-x86_64.exe")
            else None
        )
        if not qemu_bin:
            self._on_toast("QEMU n'est pas installé (installez-le via winget install SoftwareFreedomConservancy.QEMU).")
            return
        disk_path = dev["path"]
        cmd = [qemu_bin, "-m", "2048", "-drive", f"file={disk_path},format=raw,if=ide,snapshot=on"]
        try:
            subprocess.Popen(cmd)
            self._on_toast("Aperçu virtuel QEMU lancé en lecture seule.")
        except Exception as exc:
            self._on_toast(f"Erreur lancement QEMU : {exc}")

    def _run_wipe(self) -> None:
        dev = self._get_device()
        if not dev:
            return
        if self._simple_mode:
            scheme = "gpt"
            fs = "exfat"
            label = "USB_CLEAN"
            op_title = "Réparation complète de la clé USB"
            detail = "Supprime toutes les partitions et formate la clé pour restaurer 100 % de sa capacité."
        else:
            s_idx = max(0, self._cmb_wipe_scheme.currentIndex())
            f_idx = max(0, self._cmb_wipe_fs.currentIndex())
            scheme = self._wipe_schemes[s_idx] if s_idx < len(self._wipe_schemes) else "gpt"
            fs = self._wipe_filesystems[f_idx] if f_idx < len(self._wipe_filesystems) else "exfat"
            label = sanitize_volume_label(self._txt_wipe_label.text(), fs)
            op_title = f"Remise à zéro d'usine ({scheme.upper()} · {fs.upper()})"
            detail = "Toutes les données et partitions du disque seront effacées."

        job = {
            "action": "usb_wipe_reset",
            "device": dev["path"],
            "scheme": scheme,
            "filesystem": fs,
            "volume_label": label,
        }
        dlg = ConfirmDestroyDialog(
            device=dev,
            operation_title=op_title,
            detail_msg=detail,
            on_confirmed=lambda: self._on_start_job(job, False),
            simple_mode=self._simple_mode,
            parent=self,
        )
        dlg.exec()

    def sync_sensitivity(self) -> None:
        dev = self._get_device()
        busy = self._ctrl.is_busy()
        dev_ok = bool(dev and not dev.get("is_system_disk") and not dev.get("read_only"))

        self._btn_calc_hash.setEnabled(not busy)
        self._btn_bench.setEnabled(dev_ok and not busy)
        self._btn_badblocks.setEnabled(dev_ok and not busy)
        self._btn_wipe.setEnabled(dev_ok and not busy)
        self._btn_backup.setEnabled(dev_ok and not busy)
        self._btn_restore.setEnabled(dev_ok and not busy)
        self._btn_qemu.setEnabled(dev_ok and not busy)
