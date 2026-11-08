"""Multi-Boot (Ventoy) view for BootForge Windows PyQt6 UI."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bootforge_win.backend import AsyncHelper, read_ventoy_state_for_device
from bootforge_win.dialogs import ConfirmDestroyDialog, JsonPreviewDialog
from bootforge_win.theme import PartitionMapWidget, format_bytes
from engine.disk_utils import plan_partition_layout, sanitize_volume_label
from engine.ventoy_manager import build_ventoy_json


class MultiBootWinView(QWidget):
    """Multi-OS USB builder adapting between Simple 1-2-3 and Advanced 2-column mode."""

    def __init__(
        self,
        get_device: Callable[[], Optional[Dict[str, Any]]],
        is_busy: Callable[[], bool],
        on_start_job: Callable[[Dict[str, Any], bool], None],
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_device = get_device
        self._is_busy = is_busy
        self._on_start_job = on_start_job
        self._on_log = on_log
        self._on_toast = on_toast
        self._async_helper = AsyncHelper(self)

        self._simple_mode = True
        self._syncing = False
        self._last_read_device = ""
        self._entries: List[Dict[str, Any]] = []
        self._remove_relpaths: List[str] = []
        self._action_mode = "ventoy_install"

        self._build_ui()
        self.set_simple_mode(True)
        self._on_device_changed_reset()

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

        # Columns container
        self._cols_container = QWidget()
        self._cols_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self._cols_container)
        self._cols_layout.setContentsMargins(0, 0, 0, 0)
        self._cols_layout.setSpacing(14)

        # --- LEFT COLUMN: Action mode & Settings ---
        self._left_col = QWidget()
        l_lay = QVBoxLayout(self._left_col)
        l_lay.setContentsMargins(0, 0, 0, 0)
        l_lay.setSpacing(12)

        # Card: Operation mode
        self._card_action = QFrame()
        self._card_action.setObjectName("Card")
        ca_lay = QVBoxLayout(self._card_action)
        ca_lay.setContentsMargins(14, 14, 14, 14)
        ca_lay.setSpacing(10)

        self._lbl_act_title = QLabel("1. Que voulez-vous faire sur la clé ?")
        self._lbl_act_title.setObjectName("GroupTitle")
        ca_lay.addWidget(self._lbl_act_title)

        self._lbl_act_desc = QLabel("Placez plusieurs systèmes (Windows, Ubuntu, etc.) sur la même clé USB.")
        self._lbl_act_desc.setObjectName("GroupSub")
        ca_lay.addWidget(self._lbl_act_desc)

        self._btn_group_act = QButtonGroup(self)
        self._rad_install = QRadioButton("Préparer une clé multi-systèmes de zéro (efface la clé)")
        self._rad_apply = QRadioButton("Ajouter ou retirer des systèmes sans tout effacer (synchroniser)")
        self._rad_update = QRadioButton("Mettre à jour le chargeur Ventoy uniquement (sans toucher aux ISO)")

        self._btn_group_act.addButton(self._rad_install)
        self._btn_group_act.addButton(self._rad_apply)
        self._btn_group_act.addButton(self._rad_update)
        self._rad_install.setChecked(True)

        self._rad_install.toggled.connect(self._on_action_toggled)
        self._rad_apply.toggled.connect(self._on_action_toggled)
        self._rad_update.toggled.connect(self._on_action_toggled)

        ca_lay.addWidget(self._rad_install)
        ca_lay.addWidget(self._rad_apply)
        ca_lay.addWidget(self._rad_update)
        l_lay.addWidget(self._card_action)

        # Card: Container partition settings (Advanced only)
        self._card_cont = QFrame()
        self._card_cont.setObjectName("Card")
        cc_lay = QVBoxLayout(self._card_cont)
        cc_lay.setContentsMargins(14, 14, 14, 14)
        cc_lay.setSpacing(8)

        lbl_c_title = QLabel("Paramètres du conteneur Ventoy")
        lbl_c_title.setObjectName("GroupTitle")
        cc_lay.addWidget(lbl_c_title)

        h_sc = QHBoxLayout()
        h_sc.addWidget(QLabel("Table de partitions :"))
        self._cmb_scheme = QComboBox()
        self._cmb_scheme.addItems(["GPT (UEFI moderne)", "MBR (BIOS hérité / UEFI-CSM)"])
        self._cmb_scheme.currentIndexChanged.connect(lambda: self.refresh_partition_map())
        h_sc.addWidget(self._cmb_scheme, 1)
        cc_lay.addLayout(h_sc)

        self._chk_sb = QCheckBox("Prise en charge Secure Boot (VTOYEFI)")
        self._chk_sb.setChecked(True)
        self._chk_sb.toggled.connect(lambda: self.refresh_partition_map())
        cc_lay.addWidget(self._chk_sb)

        h_lbl = QHBoxLayout()
        h_lbl.addWidget(QLabel("Nom du volume (exFAT) :"))
        self._txt_label = QLineEdit("Ventoy")
        h_lbl.addWidget(self._txt_label, 1)
        cc_lay.addLayout(h_lbl)

        h_res = QHBoxLayout()
        h_res.addWidget(QLabel("Espace réservé en fin de disque (Mo) :"))
        self._spin_reserve = QSpinBox()
        self._spin_reserve.setRange(0, 16384)
        self._spin_reserve.setSingleStep(256)
        self._spin_reserve.setValue(0)
        self._spin_reserve.valueChanged.connect(lambda: self.refresh_partition_map())
        h_res.addWidget(self._spin_reserve)
        cc_lay.addLayout(h_res)
        l_lay.addWidget(self._card_cont)

        # Card: Global rules & tweaks
        self._card_rules = QFrame()
        self._card_rules.setObjectName("Card")
        cr_lay = QVBoxLayout(self._card_rules)
        cr_lay.setContentsMargins(14, 14, 14, 14)
        cr_lay.setSpacing(8)

        self._lbl_rules_title = QLabel("2. Options pratiques")
        self._lbl_rules_title.setObjectName("GroupTitle")
        cr_lay.addWidget(self._lbl_rules_title)

        self._chk_win_simple = QCheckBox("Faciliter l'installation de Windows 11 (contournement TPM / Secure Boot)")
        self._chk_win_simple.setChecked(True)
        cr_lay.addWidget(self._chk_win_simple)

        # Advanced options
        self._chk_win_nro = QCheckBox("Autoriser un compte local Windows 11 (VTOY_WIN11_BYPASS_NRO)")
        self._chk_win_nro.setChecked(True)
        cr_lay.addWidget(self._chk_win_nro)

        self._chk_tree = QCheckBox("Afficher le menu de démarrage en arborescence")
        self._chk_tree.setChecked(False)
        cr_lay.addWidget(self._chk_tree)

        self._chk_filter = QCheckBox("Masquer les fichiers cachés (._*)")
        self._chk_filter.setChecked(True)
        cr_lay.addWidget(self._chk_filter)

        self._row_timeout = QWidget()
        h_to = QHBoxLayout(self._row_timeout)
        h_to.setContentsMargins(0, 0, 0, 0)
        h_to.addWidget(QLabel("Délai de démarrage automatique (sec) :"))
        self._spin_timeout = QSpinBox()
        self._spin_timeout.setRange(0, 120)
        self._spin_timeout.setValue(0)
        h_to.addWidget(self._spin_timeout)
        cr_lay.addWidget(self._row_timeout)
        l_lay.addWidget(self._card_rules)
        l_lay.addStretch()

        self._cols_layout.addWidget(self._left_col)

        # --- RIGHT COLUMN: Systems Queue ---
        self._right_col = QWidget()
        r_lay = QVBoxLayout(self._right_col)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(10)

        # Queue Header
        q_hdr = QVBoxLayout()
        q_hdr.setSpacing(6)

        v_qt = QVBoxLayout()
        v_qt.setSpacing(2)
        self._lbl_q_title = QLabel("3. Systèmes à mettre sur la clé")
        self._lbl_q_title.setObjectName("GroupTitle")
        self._lbl_q_desc = QLabel("Ajoutez un ou plusieurs fichiers .iso (Windows, Ubuntu, Mint...).")
        self._lbl_q_desc.setObjectName("GroupSub")
        v_qt.addWidget(self._lbl_q_title)
        v_qt.addWidget(self._lbl_q_desc)
        q_hdr.addLayout(v_qt)

        h_q_acts = QHBoxLayout()
        h_q_acts.setSpacing(8)

        self._btn_read_usb = QPushButton("Lire la clé")
        self._btn_read_usb.clicked.connect(lambda: self._read_usb_state(notify=True))
        h_q_acts.addWidget(self._btn_read_usb)

        self._btn_json = QPushButton("Aperçu JSON")
        self._btn_json.clicked.connect(self._open_json_preview)
        h_q_acts.addWidget(self._btn_json)

        h_q_acts.addStretch()

        self._btn_add_iso = QPushButton("+ Ajouter un fichier")
        self._btn_add_iso.setObjectName("PrimaryBtn")
        self._btn_add_iso.clicked.connect(self._browse_isos)
        h_q_acts.addWidget(self._btn_add_iso)
        q_hdr.addLayout(h_q_acts)
        r_lay.addLayout(q_hdr)

        # Queue List Area
        self._queue_frame = QFrame()
        self._queue_frame.setObjectName("Card")
        self._queue_frame_lay = QVBoxLayout(self._queue_frame)
        self._queue_frame_lay.setContentsMargins(12, 12, 12, 12)
        self._queue_frame_lay.setSpacing(8)

        self._lbl_empty = QLabel("Aucun système ajouté pour l'instant.\nCliquez sur « + Ajouter un fichier » pour choisir vos images ISO.")
        self._lbl_empty.setObjectName("Muted")
        self._lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._queue_frame_lay.addWidget(self._lbl_empty)

        self._queue_items_layout = QVBoxLayout()
        self._queue_items_layout.setSpacing(8)
        self._queue_frame_lay.addLayout(self._queue_items_layout)
        r_lay.addWidget(self._queue_frame, 1)

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

        # Footer Actions
        h_foot = QHBoxLayout()
        h_foot.setSpacing(10)
        self._lbl_action_hint = QLabel()
        self._lbl_action_hint.setObjectName("Muted")
        h_foot.addWidget(self._lbl_action_hint, 1)

        self._btn_dry = QPushButton("Simuler (dry-run)")
        self._btn_dry.clicked.connect(lambda: self._trigger_ventoy(dry_run=True))
        h_foot.addWidget(self._btn_dry)

        self._btn_run = QPushButton("Préparer la clé")
        self._btn_run.setObjectName("PrimaryBtn")
        self._btn_run.clicked.connect(lambda: self._trigger_ventoy(dry_run=False))
        h_foot.addWidget(self._btn_run)

        main_layout.addLayout(h_foot)

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple

        if simple:
            self._cols_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self._cols_layout.setSpacing(14)
            self._lbl_act_title.setText("1. Que voulez-vous faire sur la clé ?")
            self._lbl_act_desc.setText("Placez plusieurs systèmes (Windows, Ubuntu, etc.) sur la même clé USB.")
            self._rad_install.setText("Préparer une clé multi-systèmes de zéro (efface la clé)")
            self._rad_apply.setText("Ajouter ou retirer des systèmes sans tout effacer")
            self._lbl_rules_title.setText("2. Option pratique")
            self._lbl_q_title.setText("3. Systèmes à mettre sur la clé")
            self._lbl_q_desc.setText("Ajoutez un ou plusieurs fichiers .iso (Windows, Ubuntu, Mint...).")
            self._btn_add_iso.setText("+ Ajouter un fichier")
            self._btn_run.setText("Préparer la clé")
        else:
            self._cols_layout.setDirection(QBoxLayout.Direction.LeftToRight)
            self._cols_layout.setSpacing(16)
            self._lbl_act_title.setText("Opération Ventoy")
            self._lbl_act_desc.setText("Installe le chargeur multi-OS ou synchronise vos images ISO.")
            self._rad_install.setText("Installer Ventoy (initialisation complète du disque)")
            self._rad_apply.setText("Synchroniser les ISO et ventoy.json (sans formater)")
            self._lbl_rules_title.setText("Règles globales (/ventoy/ventoy.json)")
            self._lbl_q_title.setText("File Multi-Boot et réglages par système")
            self._lbl_q_desc.setText("Gérez les images, l'alias de menu et les profils de démarrage.")
            self._btn_add_iso.setText("+ Ajouter ISO")
            self._btn_run.setText("Exécuter Ventoy")

        self._rad_update.setVisible(not simple)
        self._card_cont.setVisible(not simple)
        self._chk_win_nro.setVisible(not simple)
        self._chk_tree.setVisible(not simple)
        self._chk_filter.setVisible(not simple)
        self._row_timeout.setVisible(not simple)
        self._btn_json.setVisible(not simple)
        self._part_card.setVisible(not simple)
        self._btn_dry.setVisible(not simple)

        self._rebuild_queue_ui()
        if not simple:
            self.refresh_partition_map()
        self.sync_sensitivity()

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
        self._rad_apply.setEnabled(has_ventoy)
        self._rad_update.setEnabled(has_ventoy)
        self._btn_read_usb.setEnabled(has_ventoy and not self._is_busy())

        if has_ventoy:
            self._rad_apply.setChecked(True)
        else:
            self._rad_install.setChecked(True)
            self._entries = [e for e in self._entries if not e.get("on_usb")]
            self._remove_relpaths.clear()
            self._rebuild_queue_ui()

        if not self._simple_mode:
            self.refresh_partition_map()
        self.sync_sensitivity()

    def _on_action_toggled(self) -> None:
        if self._rad_apply.isChecked():
            self._action_mode = "ventoy_apply"
        elif self._rad_update.isChecked() and not self._simple_mode:
            self._action_mode = "ventoy_update"
        else:
            self._action_mode = "ventoy_install"

        is_inst = self._action_mode == "ventoy_install"
        self._cmb_scheme.setEnabled(is_inst)
        self._chk_sb.setEnabled(is_inst)
        self._txt_label.setEnabled(is_inst)
        self._spin_reserve.setEnabled(is_inst)
        self.sync_sensitivity()

    def refresh_partition_map(self) -> None:
        dev = self._get_device()
        disk_bytes = int(dev.get("size_bytes", 32 * 1024**3)) if dev else 32 * 1024**3
        scheme = "gpt" if self._cmb_scheme.currentIndex() == 0 else "mbr"
        reserve = int(self._spin_reserve.value())
        segs = plan_partition_layout(
            disk_bytes,
            "ventoy",
            scheme=scheme,
            filesystem="exfat",
            ventoy_reserve_mb=reserve,
        )
        badge = f"{scheme.upper()} · VTOYEFI" + (" SecureBoot" if self._chk_sb.isChecked() else "")
        self._part_map.set_layout(segs, title="Partitions Ventoy (exFAT + VTOYEFI)", badge=badge)

    def _browse_isos(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Ajouter des fichiers .iso ou .img",
            "",
            "Images disque (*.iso *.ISO *.img *.IMG *.wim *.vhd *.vhdx);;Tous les fichiers (*.*)",
        )
        added = 0
        for f in files:
            if os.path.isfile(f):
                self.add_iso_external(f)
                added += 1
        if added > 0:
            self._on_toast(f"{added} fichier(s) ajouté(s) à la clé.")

    def add_iso_external(self, file_path: str) -> None:
        if not os.path.isfile(file_path):
            return
        name = os.path.basename(file_path)
        rel_path = "/" + name
        if any(e["rel_path"] == rel_path for e in self._entries):
            return

        lower = name.lower()
        is_win = any(k in lower for k in ("win11", "win10", "windows", "server"))
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
            }
        )
        self._rebuild_queue_ui()
        self.sync_sensitivity()

    def _read_usb_state(self, notify: bool = True) -> None:
        dev = self._get_device()
        if not dev or not dev.get("ventoy_detected"):
            return
        self._btn_read_usb.setEnabled(False)
        self._on_log("info", f"Lecture du contenu Ventoy sur {dev.get('path')}...")

        def _do() -> Dict[str, Any]:
            return read_ventoy_state_for_device(dev)

        def _ok(state: Dict[str, Any]) -> None:
            self._btn_read_usb.setEnabled(True)
            vjson = state.get("ventoy_json") or {}
            alias_map = {a["image"]: a["alias"] for a in vjson.get("menu_alias") or [] if "image" in a and "alias" in a}
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
                        "unattend_profile": "none",
                    }
                )
            queued = [e for e in self._entries if not e.get("on_usb")]
            self._entries = usb_items + queued
            self._remove_relpaths.clear()
            self._rebuild_queue_ui()
            self.sync_sensitivity()
            if notify:
                self._on_toast(f"Clé lue : {len(usb_items)} système(s) trouvé(s).")

        def _err(msg: str) -> None:
            self._btn_read_usb.setEnabled(True)
            if notify:
                self._on_toast(f"Lecture impossible : {msg}")

        self._async_helper.run_fn(_do, _ok, _err)

    def _rebuild_queue_ui(self) -> None:
        # Clear previous items
        while self._queue_items_layout.count():
            it = self._queue_items_layout.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        has_items = len(self._entries) > 0
        self._lbl_empty.setVisible(not has_items)

        for entry in self._entries:
            item_frame = QFrame()
            item_frame.setObjectName("Card")
            if_lay = QVBoxLayout(item_frame)
            if_lay.setContentsMargins(12, 10, 12, 10)
            if_lay.setSpacing(6)

            h_top = QHBoxLayout()
            os_ic = "🪟" if entry["os_hint"] == "windows" else "🐧"
            loc_tag = "Déjà sur la clé" if entry["on_usb"] else "À copier"
            tag_col = "#10b981" if entry["on_usb"] else "#38bdf8"
            lbl_n = QLabel(f"<b>{os_ic} {entry['name']}</b> ({format_bytes(entry['size_bytes'])}) · <span style='color: {tag_col};'>{loc_tag}</span>")
            h_top.addWidget(lbl_n, 1)

            btn_del = QPushButton("Retirer")
            btn_del.clicked.connect(lambda _, item=entry: self._remove_entry(item))
            h_top.addWidget(btn_del)
            if_lay.addLayout(h_top)

            if not self._simple_mode:
                h_mid = QHBoxLayout()
                h_mid.addWidget(QLabel("Nom au menu :"))
                txt_al = QLineEdit(entry.get("alias", ""))
                txt_al.setPlaceholderText("Nom personnalisé dans le menu de démarrage...")
                txt_al.textChanged.connect(lambda t, item=entry: item.__setitem__("alias", t))
                h_mid.addWidget(txt_al, 1)
                if_lay.addLayout(h_mid)

                chk_def = QCheckBox("Démarrer par défaut sur ce système")
                chk_def.setChecked(bool(entry.get("default_img", False)))
                chk_def.toggled.connect(lambda c, item=entry: self._set_default_img(item, c))
                if_lay.addWidget(chk_def)

            self._queue_items_layout.addWidget(item_frame)

    def _set_default_img(self, item: Dict[str, Any], is_default: bool) -> None:
        if is_default:
            for e in self._entries:
                e["default_img"] = (e == item)
        else:
            item["default_img"] = False
        self._rebuild_queue_ui()

    def _remove_entry(self, item: Dict[str, Any]) -> None:
        if item.get("on_usb"):
            self._remove_relpaths.append(item["rel_path"])
        self._entries = [e for e in self._entries if e["rel_path"] != item["rel_path"]]
        self._rebuild_queue_ui()
        self.sync_sensitivity()

    def _collect_payload(self) -> Dict[str, Any]:
        aliases = []
        auto_install = []
        add_isos = []
        default_img = ""

        for idx, e in enumerate(self._entries, start=1):
            rel = e["rel_path"]
            if e.get("source_path") and not e.get("on_usb"):
                add_isos.append(e["source_path"])
            if e.get("alias", "").strip():
                aliases.append({"image": rel, "alias": e["alias"].strip()})
            if e.get("default_img"):
                default_img = rel

        v_cfg = {
            "win11_bypass_check": self._chk_win_simple.isChecked(),
            "win11_bypass_nro": self._chk_win_nro.isChecked(),
            "tree_view": self._chk_tree.isChecked(),
            "filter_dot_files": self._chk_filter.isChecked(),
            "menu_timeout": int(self._spin_timeout.value()),
            "default_image": default_img,
            "menu_alias": aliases,
            "auto_install": auto_install,
            "persistence": [],
        }
        return {
            "ventoy_config": v_cfg,
            "add_isos": add_isos,
            "remove_isos": list(self._remove_relpaths),
            "create_persistence": [],
            "unattend_profiles": [],
        }

    def _open_json_preview(self) -> None:
        payload = self._collect_payload()
        doc = build_ventoy_json(payload["ventoy_config"])
        dlg = JsonPreviewDialog(doc, self)
        dlg.exec()

    def sync_sensitivity(self) -> None:
        dev = self._get_device()
        busy = self._is_busy()
        dev_ok = bool(dev and not dev.get("is_system_disk") and not dev.get("read_only"))

        has_actions = len(self._entries) > 0 or self._action_mode == "ventoy_install" or len(self._remove_relpaths) > 0
        can_run = dev_ok and has_actions and not busy

        self._btn_dry.setEnabled(can_run)
        self._btn_run.setEnabled(can_run)
        self._btn_read_usb.setEnabled(bool(dev and dev.get("ventoy_detected")) and not busy)

        if busy:
            self._lbl_action_hint.setText("Opération en cours...")
        elif not dev:
            self._lbl_action_hint.setText("Branchez une clé USB pour commencer." if self._simple_mode else "Sélectionnez un périphérique USB cible ci-dessus.")
        elif dev.get("is_system_disk"):
            self._lbl_action_hint.setText("Disque système protégé : écriture bloquée.")
        elif not has_actions:
            self._lbl_action_hint.setText("Ajoutez au moins une image ISO à copier.")
        else:
            add_cnt = sum(1 for e in self._entries if not e.get("on_usb"))
            if self._action_mode == "ventoy_install":
                self._lbl_action_hint.setText(f"Prêt : Ventoy sera installé de zéro sur {dev.get('model', 'la clé')} ({add_cnt} ISO ajoutée(s)).")
            else:
                self._lbl_action_hint.setText(f"Prêt : Synchronisation de {dev.get('model', 'la clé')} ({add_cnt} nouvelle(s) ISO).")

    def _trigger_ventoy(self, dry_run: bool) -> None:
        dev = self._get_device()
        if not dev or dev.get("is_system_disk"):
            return

        payload = self._collect_payload()
        scheme = "gpt" if self._cmb_scheme.currentIndex() == 0 else "mbr"
        job = {
            "action": self._action_mode,
            "device": dev["path"],
            "scheme": scheme,
            "secure_boot": self._chk_sb.isChecked(),
            "reserve_mb": int(self._spin_reserve.value()),
            "volume_label": sanitize_volume_label(self._txt_label.text(), "exfat"),
            **payload,
        }

        if dry_run:
            self._on_start_job(job, True)
            return

        if self._action_mode == "ventoy_install":
            dlg = ConfirmDestroyDialog(
                device=dev,
                operation_title="Installation complète de Ventoy Multi-Boot",
                detail_msg="Toutes les données présentes sur la clé seront définitivement effacées pour créer les partitions Ventoy.",
                on_confirmed=lambda: self._on_start_job(job, False),
                simple_mode=self._simple_mode,
                parent=self,
            )
            dlg.exec()
        else:
            self._on_start_job(job, False)
