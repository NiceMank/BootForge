"""Dialogs for BootForge Windows PyQt6 UI (Windows Tweaks XML, Confirmation, Preflight, About)."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bootforge_ui import __version__
from bootforge_win.theme import RESOURCE_ROOT, format_bytes, load_win_prefs, save_win_prefs
from engine.disk_utils import check_system_dependencies
from engine.i18n import get_current_language, t
from engine.unattend_builder import build_autounattend_xml
from engine.updater import (
    UpdateCheckResult,
    apply_downloaded_update,
    check_for_update,
    download_verified_update,
    get_current_version,
)


class WinTweaksDialog(QDialog):
    """Windows 11 autounattend.xml options dialog with live XML preview."""

    def __init__(self, current_opts: Dict[str, Any], on_apply: Callable[[Dict[str, Any]], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("dialog_tweaks_title"))
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

        lbl_b_title = QLabel(t("dialog_tweaks_grp_bypass"))
        lbl_b_title.setObjectName("GroupTitle")
        b_lay.addWidget(lbl_b_title)

        self._chk_enabled = QCheckBox(t("dialog_tweaks_chk_enabled"))
        self._chk_enabled.setChecked(bool(self._opts.get("enabled", True)))
        b_lay.addWidget(self._chk_enabled)

        self._chk_hw = QCheckBox(t("dialog_tweaks_chk_hw"))
        self._chk_hw.setChecked(bool(self._opts.get("bypass_tpm_secureboot_ram", True)))
        b_lay.addWidget(self._chk_hw)

        self._chk_nro = QCheckBox(t("dialog_tweaks_chk_nro"))
        self._chk_nro.setChecked(bool(self._opts.get("bypass_microsoft_account", True)))
        b_lay.addWidget(self._chk_nro)

        self._chk_bitlocker = QCheckBox(t("dialog_tweaks_chk_bitlocker"))
        self._chk_bitlocker.setChecked(bool(self._opts.get("disable_bitlocker", True)))
        b_lay.addWidget(self._chk_bitlocker)

        self._chk_telemetry = QCheckBox(t("dialog_tweaks_chk_telemetry"))
        self._chk_telemetry.setChecked(bool(self._opts.get("disable_telemetry", True)))
        b_lay.addWidget(self._chk_telemetry)

        self._chk_bloat = QCheckBox(t("dialog_tweaks_chk_bloat"))
        self._chk_bloat.setChecked(bool(self._opts.get("disable_consumer_bloat", False)))
        b_lay.addWidget(self._chk_bloat)

        self._chk_key = QCheckBox(t("dialog_tweaks_chk_key"))
        self._chk_key.setChecked(bool(self._opts.get("skip_product_key", True)))
        b_lay.addWidget(self._chk_key)

        sc_layout.addWidget(grp_bypass)

        # Group Local Account
        grp_acct = QFrame()
        grp_acct.setObjectName("Card")
        a_lay = QVBoxLayout(grp_acct)
        a_lay.setContentsMargins(14, 14, 14, 14)
        a_lay.setSpacing(10)

        lbl_a_title = QLabel(t("dialog_tweaks_grp_acct"))
        lbl_a_title.setObjectName("GroupTitle")
        a_lay.addWidget(lbl_a_title)

        self._chk_local = QCheckBox(t("dialog_tweaks_chk_local"))
        self._chk_local.setChecked(bool(self._opts.get("create_local_account", False)))
        self._chk_local.toggled.connect(self._sync_local_fields)
        a_lay.addWidget(self._chk_local)

        h_user = QHBoxLayout()
        h_user.addWidget(QLabel(t("dialog_tweaks_lbl_user")))
        self._txt_user = QLineEdit(str(self._opts.get("local_username") or "Utilisateur"))
        h_user.addWidget(self._txt_user)
        a_lay.addLayout(h_user)

        h_pass = QHBoxLayout()
        h_pass.addWidget(QLabel(t("dialog_tweaks_lbl_pass")))
        self._txt_pass = QLineEdit(str(self._opts.get("local_password") or ""))
        self._txt_pass.setEchoMode(QLineEdit.EchoMode.Password)
        h_pass.addWidget(self._txt_pass)
        a_lay.addLayout(h_pass)

        h_loc = QHBoxLayout()
        h_loc.addWidget(QLabel(t("dialog_tweaks_lbl_loc")))
        self._cmb_loc = QComboBox()
        self._loc_keys = ["fr-FR", "en-US", "fr-CA", "fr-BE", "fr-CH"]
        self._cmb_loc.addItems(["Français (fr-FR)", "English (en-US)", "Français Canada (fr-CA)", "Français Belgique (fr-BE)", "Français Suisse (fr-CH)"])
        cur_l = str(self._opts.get("locale") or ("en-US" if get_current_language() == "en" else "fr-FR"))
        if cur_l in self._loc_keys:
            self._cmb_loc.setCurrentIndex(self._loc_keys.index(cur_l))
        h_loc.addWidget(self._cmb_loc)
        a_lay.addLayout(h_loc)

        sc_layout.addWidget(grp_acct)
        sc_layout.addStretch()

        scroll.setWidget(scroll_content)
        v_opts.addWidget(scroll)
        self._tabs.addTab(tab_opts, t("dialog_tweaks_tab_opts"))

        # Tab 2: XML Preview
        tab_xml = QWidget()
        v_xml = QVBoxLayout(tab_xml)
        v_xml.setContentsMargins(10, 10, 10, 10)
        v_xml.setSpacing(8)

        h_xml_top = QHBoxLayout()
        h_xml_top.addWidget(QLabel(t("dialog_tweaks_lbl_xml_preview")))
        btn_copy_xml = QPushButton(t("dialog_tweaks_btn_copy_xml"))
        btn_copy_xml.clicked.connect(self._copy_xml)
        h_xml_top.addWidget(btn_copy_xml)
        v_xml.addLayout(h_xml_top)

        self._xml_view = QPlainTextEdit()
        self._xml_view.setObjectName("CodeView")
        self._xml_view.setReadOnly(True)
        v_xml.addWidget(self._xml_view)

        self._tabs.addTab(tab_xml, t("dialog_tweaks_tab_xml"))
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Footer Buttons
        h_btns = QHBoxLayout()
        h_btns.addStretch()
        btn_cancel = QPushButton(t("dialog_tweaks_btn_cancel"))
        btn_cancel.clicked.connect(self.reject)
        h_btns.addWidget(btn_cancel)

        btn_save = QPushButton(t("dialog_tweaks_btn_save"))
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
        self.setWindowTitle(t("dialog_confirm_title_simple") if simple_mode else t("dialog_confirm_title_adv"))
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
            t("dialog_confirm_intro_simple", detail=detail_msg)
            if simple_mode
            else t("dialog_confirm_intro_adv", detail=detail_msg)
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

        i_lay.addWidget(QLabel(t("dialog_confirm_action_lbl", action=operation_title)))
        dev_desc = (
            f"{device.get('model', 'USB')} ({format_bytes(device.get('size_bytes', 0))})"
            if simple_mode
            else f"{device.get('model', 'Disk')} ({device.get('path', '')}) · {format_bytes(device.get('size_bytes', 0))}"
        )
        i_lay.addWidget(QLabel(t("dialog_confirm_target_lbl", dev=dev_desc)))
        root.addWidget(info_card)

        # Typing confirmation
        prompt_txt = t("dialog_confirm_prompt_msg", word=t("dialog_confirm_word"))
        root.addWidget(QLabel(prompt_txt))
        self._txt_confirm = QLineEdit()
        self._txt_confirm.setPlaceholderText(t("dialog_confirm_word"))
        root.addWidget(self._txt_confirm)

        h_btns = QHBoxLayout()
        h_btns.addStretch()
        btn_cancel = QPushButton(t("dialog_confirm_btn_cancel"))
        btn_cancel.clicked.connect(self.reject)
        h_btns.addWidget(btn_cancel)

        self._btn_exec = QPushButton(t("dialog_confirm_btn_exec"))
        self._btn_exec.setObjectName("DangerBtn")
        self._btn_exec.setEnabled(False)
        self._btn_exec.clicked.connect(self._accept_and_run)
        h_btns.addWidget(self._btn_exec)
        root.addLayout(h_btns)

        self._txt_confirm.textChanged.connect(self._on_text_changed)
        self._txt_confirm.returnPressed.connect(self._accept_and_run)

    def _on_text_changed(self, text: str) -> None:
        self._btn_exec.setEnabled(text.strip().upper() in ("EFFACER", "ERASE"))

    def _accept_and_run(self) -> None:
        if self._txt_confirm.text().strip().upper() in ("EFFACER", "ERASE"):
            self.accept()
            self._on_confirmed()


class PreflightDialog(QDialog):
    """System dependencies inspection dialog for Windows."""

    def __init__(self, deps: Optional[Dict[str, Any]], on_refresh: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("dialog_preflight_win_title"))
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
        lbl1 = QLabel(t("dialog_preflight_win_core"))
        lbl1.setObjectName("GroupTitle")
        g1.addWidget(lbl1)

        for c in deps.get("core", []):
            h = QHBoxLayout()
            h.addWidget(QLabel(f"<b>{c['name']}</b> : {c['purpose']}"))
            h.addStretch()
            status = QLabel(t("dialog_preflight_status_ok") if c["installed"] else t("dialog_preflight_status_miss"))
            h.addWidget(status)
            g1.addLayout(h)
        c_lay.addWidget(grp_core)

        # Embedded Assets
        grp_assets = QFrame()
        grp_assets.setObjectName("Card")
        g2 = QVBoxLayout(grp_assets)
        g2.setContentsMargins(14, 14, 14, 14)
        g2.setSpacing(8)
        lbl2 = QLabel(t("dialog_preflight_win_embedded"))
        lbl2.setObjectName("GroupTitle")
        g2.addWidget(lbl2)

        uefi_ok = bool(deps.get("uefi_ntfs_cached"))
        h_u = QHBoxLayout()
        h_u.addWidget(QLabel(f"<b>{t('dialog_preflight_win_uefi')}</b>"))
        h_u.addStretch()
        h_u.addWidget(QLabel(t("dialog_preflight_status_ready") if uefi_ok else t("dialog_preflight_status_dl")))
        g2.addLayout(h_u)
        c_lay.addWidget(grp_assets)

        # Optional Tools
        grp_opt = QFrame()
        grp_opt.setObjectName("Card")
        g3 = QVBoxLayout(grp_opt)
        g3.setContentsMargins(14, 14, 14, 14)
        g3.setSpacing(8)
        lbl3 = QLabel(t("dialog_preflight_win_opt"))
        lbl3.setObjectName("GroupTitle")
        g3.addWidget(lbl3)

        for opt in deps.get("optional", []):
            h = QHBoxLayout()
            cmd_note = f" (<code>{opt.get('install_cmd', '')}</code>)" if not opt["installed"] and opt.get("install_cmd") else ""
            h.addWidget(QLabel(f"<b>{opt['name']}</b> : {opt['purpose']}{cmd_note}"))
            h.addStretch()
            h.addWidget(QLabel(t("dialog_preflight_status_ok") if opt["installed"] else t("dialog_preflight_status_opt")))
            g3.addLayout(h)
        c_lay.addWidget(grp_opt)

        scroll.setWidget(content)
        root.addWidget(scroll)

        h_bot = QHBoxLayout()
        h_bot.addStretch()
        btn_ref = QPushButton(t("dialog_preflight_btn_refresh"))
        btn_ref.clicked.connect(lambda: (self._on_refresh(), self.accept()))
        h_bot.addWidget(btn_ref)
        btn_close = QPushButton(t("dialog_preflight_btn_close"))
        btn_close.clicked.connect(self.accept)
        h_bot.addWidget(btn_close)
        root.addLayout(h_bot)


class JsonPreviewDialog(QDialog):
    """Aperçu de configuration JSON pour Ventoy (/ventoy/ventoy.json)."""

    def __init__(self, json_data: Dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("dialog_json_title"))
        self.setMinimumSize(580, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        h_top = QHBoxLayout()
        h_top.addWidget(QLabel(t("dialog_json_intro")))
        h_top.addStretch()
        btn_cp = QPushButton(t("dialog_json_btn_copy"))
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
        btn_ok = QPushButton(t("dialog_json_btn_close"))
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
        self.setWindowTitle(t("dialog_about_title"))
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

        lbl_ver = QLabel(t("dialog_about_edition_win", ver=__version__))
        lbl_ver.setObjectName("Muted")
        lbl_ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_ver)

        lbl_desc = QLabel(t("dialog_about_desc"))
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_desc)

        lbl_links = QLabel(
            f'<a href="https://github.com/NiceMank/BootForge" style="color: #D92654; text-decoration: none;">{t("dialog_about_link_repo")}</a> &nbsp;·&nbsp; '
            f'<a href="https://github.com/NiceMank/BootForge/issues" style="color: #D92654; text-decoration: none;">{t("dialog_about_link_issues")}</a> &nbsp;·&nbsp; '
            f'<span style="color: #9BA3B4;">{t("dialog_about_link_license")}</span>'
        )
        lbl_links.setOpenExternalLinks(True)
        lbl_links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(lbl_links)

        btn_close = QPushButton(t("dialog_about_btn_close"))
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close)


class UpdatesWinDialog(QDialog):
    """GitHub Releases cryptographic auto-updater dialog for Windows PyQt6 UI."""

    _sig_checked = pyqtSignal(object)
    _sig_progress = pyqtSignal(int)
    _sig_install_done = pyqtSignal(bool, object, str)

    def __init__(
        self,
        is_busy_fn: Callable[[], bool],
        on_toast: Callable[[str], None],
        initial_result: Optional[UpdateCheckResult] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("update_title"))
        self.setMinimumSize(560, 440)
        self._is_busy = is_busy_fn
        self._on_toast = on_toast
        self._result: Optional[UpdateCheckResult] = initial_result
        self._busy_net = False

        self._sig_checked.connect(self._on_check_done)
        self._sig_progress.connect(self._on_progress_val)
        self._sig_install_done.connect(self._on_install_done)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("Card")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(12, 12, 12, 12)
        c_lay.setSpacing(8)

        self._lbl_ver = QLabel(t("update_current_ver", ver=get_current_version()))
        self._lbl_ver.setObjectName("GroupTitle")
        c_lay.addWidget(self._lbl_ver)

        self._lbl_status = QLabel(t("update_status_idle"))
        self._lbl_status.setWordWrap(True)
        c_lay.addWidget(self._lbl_status)

        prefs = load_win_prefs()
        self._chk_auto = QCheckBox(t("update_auto_chk"))
        self._chk_auto.setChecked(bool(prefs.get("auto_check_updates", True)))
        self._chk_auto.toggled.connect(lambda v: save_win_prefs({"auto_check_updates": bool(v)}))
        c_lay.addWidget(self._chk_auto)
        root.addWidget(card)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._txt_changelog = QPlainTextEdit()
        self._txt_changelog.setObjectName("CodeView")
        self._txt_changelog.setReadOnly(True)
        root.addWidget(self._txt_changelog, 1)

        btns = QHBoxLayout()
        self._btn_check = QPushButton(t("update_btn_check"))
        self._btn_check.clicked.connect(self._start_check)
        btns.addWidget(self._btn_check)

        self._btn_skip = QPushButton(t("update_btn_skip"))
        self._btn_skip.setVisible(False)
        self._btn_skip.clicked.connect(self._skip_version)
        btns.addWidget(self._btn_skip)

        btns.addStretch()

        self._btn_install = QPushButton(t("update_btn_install"))
        self._btn_install.setObjectName("PrimaryBtn")
        self._btn_install.setEnabled(False)
        self._btn_install.clicked.connect(self._start_install)
        btns.addWidget(self._btn_install)

        btn_close = QPushButton(t("update_btn_close"))
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        root.addLayout(btns)

        if initial_result is not None:
            self._apply_result(initial_result)

    def _start_check(self) -> None:
        if self._busy_net:
            return
        self._busy_net = True
        self._btn_check.setEnabled(False)
        self._btn_install.setEnabled(False)
        self._lbl_status.setText(t("update_status_checking"))

        import threading

        def _worker() -> None:
            res = check_for_update()
            self._sig_checked.emit(res)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_check_done(self, res: UpdateCheckResult) -> None:
        self._busy_net = False
        self._btn_check.setEnabled(True)
        self._apply_result(res)

    def _apply_result(self, res: UpdateCheckResult) -> None:
        self._result = res
        self._txt_changelog.setPlainText(res.changelog or "")
        if res.status == "up_to_date":
            self._lbl_status.setText(t("update_status_up_to_date", ver=res.current_version))
            self._btn_install.setEnabled(False)
            self._btn_skip.setVisible(False)
        elif res.status == "available" and res.verified:
            self._btn_skip.setVisible(True)
            if self._is_busy():
                self._lbl_status.setText(t("update_status_busy"))
                self._btn_install.setEnabled(False)
            else:
                self._lbl_status.setText(t("update_status_available", ver=res.latest_version, asset=res.asset_name))
                self._btn_install.setEnabled(True)
        else:
            self._lbl_status.setText(t("update_status_blocked", reason=res.error_message or res.status))
            self._btn_install.setEnabled(False)
            self._btn_skip.setVisible(False)

    def _skip_version(self) -> None:
        if self._result and self._result.latest_version:
            save_win_prefs({"skipped_version": self._result.latest_version})
            self.accept()

    def _on_progress_val(self, pct: int) -> None:
        self._progress.setValue(pct)

    def _start_install(self) -> None:
        if not self._result or self._busy_net:
            return
        if self._is_busy():
            self._lbl_status.setText(t("update_status_busy"))
            return
        res = self._result
        self._busy_net = True
        self._btn_install.setEnabled(False)
        self._btn_check.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._lbl_status.setText(t("update_status_downloading"))

        import threading

        def _prog(received: int, total: int, _speed: float) -> None:
            pct = int((received / total) * 100) if total > 0 else 0
            self._sig_progress.emit(min(max(pct, 0), 100))

        def _worker() -> None:
            try:
                asset_path = download_verified_update(res, progress_cb=_prog, is_busy_fn=self._is_busy)
                outcome = apply_downloaded_update(asset_path, res, is_busy_fn=self._is_busy)
                self._sig_install_done.emit(True, outcome, "")
            except Exception as exc:
                self._sig_install_done.emit(False, {}, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_install_done(self, ok: bool, outcome: Dict[str, Any], err: str) -> None:
        self._busy_net = False
        self._btn_check.setEnabled(True)
        if not ok:
            self._lbl_status.setText(t("update_status_blocked", reason=err))
            self._on_toast(err)
            return
        self._progress.setValue(100)
        act = outcome.get("action")
        if act == "launched_win_setup":
            msg = t("update_status_win_launched")
            self._lbl_status.setText(msg)
            self._on_toast(msg)
            app = QApplication.instance()
            if app is not None:
                app.quit()
        elif act == "installed_deb":
            msg = t("update_status_installed_deb")
            self._lbl_status.setText(msg)
            self._on_toast(msg)
        else:
            msg = t("update_status_extracted", path=outcome.get("path", ""))
            self._lbl_status.setText(msg)
            self._on_toast(msg)
