"""Dialogs for BootForge Windows PyQt6 UI (Windows Tweaks XML, Confirmation, Preflight, About)."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bootforge_ui import __version__
from bootforge_win.theme import RESOURCE_ROOT, format_bytes
from engine.disk_utils import check_system_dependencies
from engine.unattend_builder import build_autounattend_xml


class WinTweaksDialog(QDialog):
    """Windows 11 autounattend.xml options dialog with live XML preview."""

    def __init__(self, current_opts: Dict[str, Any], on_apply: Callable[[Dict[str, Any]], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paramètres Windows 11 (autounattend.xml)")
        self.setMinimumSize(660, 580)
        self.resize(700, 620)
        self._on_apply = on_apply
        self._opts = dict(current_opts)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self._tabs = QTabWidget(self)
        root.addWidget(self._tabs)

        # Tab 1: Options
        tab_opts = QWidget()
        v_opts = QVBoxLayout(tab_opts)
        v_opts.setContentsMargins(10, 10, 10, 10)
        v_opts.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        sc_layout = QVBoxLayout(scroll_content)
        sc_layout.setSpacing(12)

        # Group Bypass
        grp_bypass = QFrame()
        grp_bypass.setObjectName("Card")
        b_lay = QVBoxLayout(grp_bypass)
        b_lay.setContentsMargins(14, 14, 14, 14)
        b_lay.setSpacing(10)

        lbl_b_title = QLabel("Contournements Windows 11 (24H2 / 25H2)")
        lbl_b_title.setObjectName("GroupTitle")
        b_lay.addWidget(lbl_b_title)

        self._chk_enabled = QCheckBox("Activer l'injection autounattend.xml sur la clé")
        self._chk_enabled.setChecked(bool(self._opts.get("enabled", True)))
        b_lay.addWidget(self._chk_enabled)

        self._chk_hw = QCheckBox("Ignorer TPM 2.0, Secure Boot, RAM et CPU (BypassTPMCheck)")
        self._chk_hw.setChecked(bool(self._opts.get("bypass_tpm_secureboot_ram", True)))
        b_lay.addWidget(self._chk_hw)

        self._chk_nro = QCheckBox("Autoriser l'installation sans compte Microsoft (BypassNRO)")
        self._chk_nro.setChecked(bool(self._opts.get("bypass_microsoft_account", True)))
        b_lay.addWidget(self._chk_nro)

        self._chk_bitlocker = QCheckBox("Désactiver le chiffrement automatique BitLocker")
        self._chk_bitlocker.setChecked(bool(self._opts.get("disable_bitlocker", True)))
        b_lay.addWidget(self._chk_bitlocker)

        self._chk_telemetry = QCheckBox("Désactiver les questions de télémétrie et suivi")
        self._chk_telemetry.setChecked(bool(self._opts.get("disable_telemetry", True)))
        b_lay.addWidget(self._chk_telemetry)

        self._chk_bloat = QCheckBox("Désactiver les applications suggérées et widgets promotionnels")
        self._chk_bloat.setChecked(bool(self._opts.get("disable_consumer_bloat", False)))
        b_lay.addWidget(self._chk_bloat)

        self._chk_key = QCheckBox("Passer l'écran de saisie de clé produit (activation ultérieure)")
        self._chk_key.setChecked(bool(self._opts.get("skip_product_key", True)))
        b_lay.addWidget(self._chk_key)

        sc_layout.addWidget(grp_bypass)

        # Group Local Account
        grp_acct = QFrame()
        grp_acct.setObjectName("Card")
        a_lay = QVBoxLayout(grp_acct)
        a_lay.setContentsMargins(14, 14, 14, 14)
        a_lay.setSpacing(10)

        lbl_a_title = QLabel("Compte local et langue")
        lbl_a_title.setObjectName("GroupTitle")
        a_lay.addWidget(lbl_a_title)

        self._chk_local = QCheckBox("Créer un compte administrateur local automatiquement")
        self._chk_local.setChecked(bool(self._opts.get("create_local_account", False)))
        self._chk_local.toggled.connect(self._sync_local_fields)
        a_lay.addWidget(self._chk_local)

        h_user = QHBoxLayout()
        h_user.addWidget(QLabel("Nom d'utilisateur :"))
        self._txt_user = QLineEdit(str(self._opts.get("local_username") or "Utilisateur"))
        h_user.addWidget(self._txt_user)
        a_lay.addLayout(h_user)

        h_pass = QHBoxLayout()
        h_pass.addWidget(QLabel("Mot de passe (optionnel) :"))
        self._txt_pass = QLineEdit(str(self._opts.get("local_password") or ""))
        self._txt_pass.setEchoMode(QLineEdit.EchoMode.Password)
        h_pass.addWidget(self._txt_pass)
        a_lay.addLayout(h_pass)

        h_loc = QHBoxLayout()
        h_loc.addWidget(QLabel("Langue et clavier :"))
        self._cmb_loc = QComboBox()
        self._loc_keys = ["fr-FR", "en-US", "fr-CA", "fr-BE", "fr-CH"]
        self._cmb_loc.addItems(["Français (fr-FR)", "English (en-US)", "Français Canada (fr-CA)", "Français Belgique (fr-BE)", "Français Suisse (fr-CH)"])
        cur_l = str(self._opts.get("locale") or "fr-FR")
        if cur_l in self._loc_keys:
            self._cmb_loc.setCurrentIndex(self._loc_keys.index(cur_l))
        h_loc.addWidget(self._cmb_loc)
        a_lay.addLayout(h_loc)

        sc_layout.addWidget(grp_acct)
        sc_layout.addStretch()

        scroll.setWidget(scroll_content)
        v_opts.addWidget(scroll)
        self._tabs.addTab(tab_opts, "Options")

        # Tab 2: XML Preview
        tab_xml = QWidget()
        v_xml = QVBoxLayout(tab_xml)
        v_xml.setContentsMargins(10, 10, 10, 10)
        v_xml.setSpacing(8)

        h_xml_top = QHBoxLayout()
        h_xml_top.addWidget(QLabel("Aperçu généré pour autounattend.xml :"))
        btn_copy_xml = QPushButton("Copier le code XML")
        btn_copy_xml.clicked.connect(self._copy_xml)
        h_xml_top.addWidget(btn_copy_xml)
        v_xml.addLayout(h_xml_top)

        self._xml_view = QPlainTextEdit()
        self._xml_view.setObjectName("CodeView")
        self._xml_view.setReadOnly(True)
        v_xml.addWidget(self._xml_view)

        self._tabs.addTab(tab_xml, "Aperçu XML")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Footer Buttons
        h_btns = QHBoxLayout()
        h_btns.addStretch()
        btn_cancel = QPushButton("Annuler")
        btn_cancel.clicked.connect(self.reject)
        h_btns.addWidget(btn_cancel)

        btn_save = QPushButton("Enregistrer les options")
        btn_save.setObjectName("PrimaryBtn")
        btn_save.clicked.connect(self._handle_save)
        h_btns.addWidget(btn_save)
        root.addLayout(h_btns)

        self._sync_local_fields(self._chk_local.isChecked())

    def _sync_local_fields(self, checked: bool) -> None:
        self._txt_user.setEnabled(checked)
        self._txt_pass.setEnabled(checked)

    def _collect_data(self) -> Dict[str, Any]:
        l_idx = max(0, self._cmb_loc.currentIndex())
        loc = self._loc_keys[l_idx] if l_idx < len(self._loc_keys) else "fr-FR"
        return {
            "enabled": self._chk_enabled.isChecked(),
            "bypass_tpm_secureboot_ram": self._chk_hw.isChecked(),
            "bypass_microsoft_account": self._chk_nro.isChecked(),
            "disable_bitlocker": self._chk_bitlocker.isChecked(),
            "disable_telemetry": self._chk_telemetry.isChecked(),
            "disable_consumer_bloat": self._chk_bloat.isChecked(),
            "skip_product_key": self._chk_key.isChecked(),
            "create_local_account": self._chk_local.isChecked(),
            "local_username": self._txt_user.text().strip() or "Utilisateur",
            "local_password": self._txt_pass.text(),
            "locale": loc,
            "timezone": "Romance Standard Time",
        }

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1:
            try:
                xml_txt = build_autounattend_xml(self._collect_data())
                self._xml_view.setPlainText(xml_txt)
            except Exception as exc:
                self._xml_view.setPlainText(f"Erreur de génération : {exc}")

    def _copy_xml(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._xml_view.toPlainText())

    def _handle_save(self) -> None:
        data = self._collect_data()
        self._on_apply(data)
        self.accept()


class ConfirmDestroyDialog(QDialog):
    """Safety confirmation dialog before repartitioning/formatting a USB drive."""

    def __init__(
        self,
        device: Dict[str, Any],
        operation_title: str,
        detail_msg: str,
        on_confirmed: Callable[[], None],
        simple_mode: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirmer l'effacement" if simple_mode else "Confirmation requise")
        self.setMinimumWidth(480)
        self._on_confirmed = on_confirmed

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # Warning banner
        banner = QFrame()
        banner.setObjectName("Card")
        b_lay = QHBoxLayout(banner)
        b_lay.setContentsMargins(12, 12, 12, 12)
        b_lay.setSpacing(12)

        lbl_icon = QLabel("⚠️")
        f_ic = QFont()
        f_ic.setPointSize(22)
        lbl_icon.setFont(f_ic)
        b_lay.addWidget(lbl_icon)

        intro = (
            f"Tout le contenu de cette clé USB va être effacé.\n{detail_msg}"
            if simple_mode
            else f"Toutes les partitions existantes sur ce disque vont être supprimées.\n{detail_msg}"
        )
        lbl_msg = QLabel(intro)
        lbl_msg.setWordWrap(True)
        b_lay.addWidget(lbl_msg)
        root.addWidget(banner)

        # Info card
        info_card = QFrame()
        info_card.setObjectName("Card")
        i_lay = QVBoxLayout(info_card)
        i_lay.setContentsMargins(12, 12, 12, 12)
        i_lay.setSpacing(6)

        i_lay.addWidget(QLabel(f"Action : <b>{operation_title}</b>"))
        dev_desc = (
            f"{device.get('model', 'Clé USB')} ({format_bytes(device.get('size_bytes', 0))})"
            if simple_mode
            else f"{device.get('model', 'Disque')} ({device.get('path', '')}) · {format_bytes(device.get('size_bytes', 0))}"
        )
        i_lay.addWidget(QLabel(f"Périphérique cible : <b>{dev_desc}</b>"))
        root.addWidget(info_card)

        # Typing confirmation
        root.addWidget(QLabel("Pour confirmer l'opération, tapez <b>EFFACER</b> ci-dessous :"))
        self._txt_confirm = QLineEdit()
        self._txt_confirm.setPlaceholderText("EFFACER")
        root.addWidget(self._txt_confirm)

        h_btns = QHBoxLayout()
        h_btns.addStretch()
        btn_cancel = QPushButton("Annuler")
        btn_cancel.clicked.connect(self.reject)
        h_btns.addWidget(btn_cancel)

        self._btn_exec = QPushButton("Effacer et démarrer")
        self._btn_exec.setObjectName("DangerBtn")
        self._btn_exec.setEnabled(False)
        self._btn_exec.clicked.connect(self._accept_and_run)
        h_btns.addWidget(self._btn_exec)
        root.addLayout(h_btns)

        self._txt_confirm.textChanged.connect(self._on_text_changed)
        self._txt_confirm.returnPressed.connect(self._accept_and_run)

    def _on_text_changed(self, text: str) -> None:
        self._btn_exec.setEnabled(text.strip().upper() == "EFFACER")

    def _accept_and_run(self) -> None:
        if self._txt_confirm.text().strip().upper() == "EFFACER":
            self.accept()
            self._on_confirmed()


class PreflightDialog(QDialog):
    """System dependencies inspection dialog for Windows."""

    def __init__(self, deps: Optional[Dict[str, Any]], on_refresh: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Outils système et composants")
        self.setMinimumSize(560, 480)
        self._on_refresh = on_refresh

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        c_lay = QVBoxLayout(content)
        c_lay.setSpacing(12)

        if not deps:
            deps = check_system_dependencies()

        # Core Tools
        grp_core = QFrame()
        grp_core.setObjectName("Card")
        g1 = QVBoxLayout(grp_core)
        g1.setContentsMargins(14, 14, 14, 14)
        g1.setSpacing(8)
        lbl1 = QLabel("Outils Windows natifs indispensables")
        lbl1.setObjectName("GroupTitle")
        g1.addWidget(lbl1)

        for c in deps.get("core", []):
            h = QHBoxLayout()
            h.addWidget(QLabel(f"<b>{c['name']}</b> : {c['purpose']}"))
            h.addStretch()
            status = QLabel("✅ Présent" if c["installed"] else "❌ Manquant")
            h.addWidget(status)
            g1.addLayout(h)
        c_lay.addWidget(grp_core)

        # Embedded Assets
        grp_assets = QFrame()
        grp_assets.setObjectName("Card")
        g2 = QVBoxLayout(grp_assets)
        g2.setContentsMargins(14, 14, 14, 14)
        g2.setSpacing(8)
        lbl2 = QLabel("Composants BootForge embarqués")
        lbl2.setObjectName("GroupTitle")
        g2.addWidget(lbl2)

        uefi_ok = bool(deps.get("uefi_ntfs_cached"))
        h_u = QHBoxLayout()
        h_u.addWidget(QLabel("<b>Chargeur UEFI:NTFS</b> (démarrage Windows NTFS)"))
        h_u.addStretch()
        h_u.addWidget(QLabel("✅ Prêt" if uefi_ok else "⚡ Téléchargement auto"))
        g2.addLayout(h_u)
        c_lay.addWidget(grp_assets)

        # Optional Tools
        grp_opt = QFrame()
        grp_opt.setObjectName("Card")
        g3 = QVBoxLayout(grp_opt)
        g3.setContentsMargins(14, 14, 14, 14)
        g3.setSpacing(8)
        lbl3 = QLabel("Outils facultatifs (recommandés)")
        lbl3.setObjectName("GroupTitle")
        g3.addWidget(lbl3)

        for opt in deps.get("optional", []):
            h = QHBoxLayout()
            cmd_note = f" (<code>{opt.get('install_cmd', '')}</code>)" if not opt["installed"] and opt.get("install_cmd") else ""
            h.addWidget(QLabel(f"<b>{opt['name']}</b> : {opt['purpose']}{cmd_note}"))
            h.addStretch()
            h.addWidget(QLabel("✅ Installé" if opt["installed"] else "⚪ Optionnel"))
            g3.addLayout(h)
        c_lay.addWidget(grp_opt)

        scroll.setWidget(content)
        root.addWidget(scroll)

        h_bot = QHBoxLayout()
        h_bot.addStretch()
        btn_ref = QPushButton("Actualiser")
        btn_ref.clicked.connect(lambda: (self._on_refresh(), self.accept()))
        h_bot.addWidget(btn_ref)
        btn_close = QPushButton("Fermer")
        btn_close.clicked.connect(self.accept)
        h_bot.addWidget(btn_close)
        root.addLayout(h_bot)


class JsonPreviewDialog(QDialog):
    """Aperçu de configuration JSON pour Ventoy (/ventoy/ventoy.json)."""

    def __init__(self, json_data: Dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aperçu /ventoy/ventoy.json")
        self.setMinimumSize(580, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        h_top = QHBoxLayout()
        h_top.addWidget(QLabel("Configuration générée automatiquement pour la clé Ventoy :"))
        h_top.addStretch()
        btn_cp = QPushButton("Copier")
        btn_cp.clicked.connect(self._copy)
        h_top.addWidget(btn_cp)
        root.addLayout(h_top)

        self._view = QPlainTextEdit()
        self._view.setObjectName("CodeView")
        self._view.setReadOnly(True)
        self._text = json.dumps(json_data, indent=2, ensure_ascii=False)
        self._view.setPlainText(self._text)
        root.addWidget(self._view)

        h_bot = QHBoxLayout()
        h_bot.addStretch()
        btn_ok = QPushButton("Fermer")
        btn_ok.clicked.connect(self.accept)
        h_bot.addWidget(btn_ok)
        root.addLayout(h_bot)

    def _copy(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._text)


class AboutDialog(QDialog):
    """About dialog for BootForge Windows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("À propos de BootForge")
        self.setFixedSize(480, 320)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_path = RESOURCE_ROOT / "assets" / "icon.png"
        lbl_logo = QLabel()
        if icon_path.is_file():
            lbl_logo.setPixmap(QIcon(str(icon_path)).pixmap(64, 64))
        lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_logo)

        lbl_title = QLabel("BootForge")
        f_t = QFont()
        f_t.setPointSize(16)
        f_t.setBold(True)
        lbl_title.setFont(f_t)
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_title)

        lbl_ver = QLabel(f"Version {__version__} · Windows Edition (64 bits)")
        lbl_ver.setObjectName("Muted")
        lbl_ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_ver)

        lbl_desc = QLabel(
            "Studio de création de clés USB bootables (Rufus direct) et multi-systèmes (Ventoy).\n"
            "Conçu et développé par NiceMank."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_desc)

        lbl_links = QLabel(
            '<a href="https://github.com/NiceMank/BootForge" style="color: #D92654; text-decoration: none;">Dépôt GitHub</a> &nbsp;·&nbsp; '
            '<a href="https://github.com/NiceMank/BootForge/issues" style="color: #D92654; text-decoration: none;">Signaler un bug</a> &nbsp;·&nbsp; '
            '<span style="color: #9BA3B4;">Licence GPL-3.0</span>'
        )
        lbl_links.setOpenExternalLinks(True)
        lbl_links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_links)

        btn_close = QPushButton("Fermer")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close)
