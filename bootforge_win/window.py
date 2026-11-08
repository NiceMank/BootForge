"""Main Window and Application runner for BootForge Windows PyQt6 UI."""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bootforge_ui import __version__
from bootforge_win.backend import AsyncHelper, WinTaskController
from bootforge_win.dialogs import AboutDialog, PreflightDialog
from bootforge_win.theme import (
    RESOURCE_ROOT,
    build_qss,
    format_bytes,
    format_speed,
    load_win_prefs,
    save_win_prefs,
)
from bootforge_win.views_catalog import CatalogWinView
from bootforge_win.views_direct import DirectFlashWinView
from bootforge_win.views_toolbox import ToolboxWinView
from bootforge_win.views_ventoy import MultiBootWinView
from engine.disk_utils import check_system_dependencies, list_block_devices
from engine.i18n import (
    get_current_language,
    init_language,
    set_current_language,
    subscribe_language_changed,
    t,
)


class BootForgeMainWindow(QMainWindow):
    """Main window for BootForge Windows Edition."""

    def __init__(self, initial_iso: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BootForge")
        self.setMinimumSize(880, 640)
        self.resize(1120, 780)

        # Set App Icon
        ico_file = RESOURCE_ROOT / "assets" / "icon.ico"
        png_file = RESOURCE_ROOT / "assets" / "icon.png"
        if ico_file.is_file():
            self.setWindowIcon(QIcon(str(ico_file)))
        elif png_file.is_file():
            self.setWindowIcon(QIcon(str(png_file)))

        self._task_ctrl = WinTaskController(self)
        self._async_helper = AsyncHelper(self)
        self._devices: List[Dict[str, Any]] = []
        self._visible_devices: List[Dict[str, Any]] = []
        self._selected_device_path = ""
        self._show_locked = False
        self._log_count = 0
        self._syncing_dropdown = False

        prefs = load_win_prefs()
        self._simple_mode = prefs.get("mode", "simple") != "advanced"
        self._dark_theme = prefs.get("theme", "dark") != "light"

        self._build_ui()
        self.apply_theme()
        self.set_simple_mode(self._simple_mode, persist=False)

        # Wire task controller signals
        self._task_ctrl.telemetry.connect(self._handle_telemetry)
        self._task_ctrl.job_done.connect(self._handle_job_done)
        self._task_ctrl.busy_changed.connect(self._handle_busy_changed)

        # Initial device scan
        self.refresh_devices()

        if initial_iso and os.path.isfile(initial_iso):
            self._view_direct.load_iso_external(initial_iso)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        main_vbox = QVBoxLayout(central)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(0)

        # ================= TOP HEADER =================
        top_header = QFrame()
        top_header.setObjectName("TopHeader")
        th_lay = QHBoxLayout(top_header)
        th_lay.setContentsMargins(16, 8, 16, 8)
        th_lay.setSpacing(12)

        # Branding
        h_brand = QHBoxLayout()
        h_brand.setSpacing(8)
        lbl_logo = QLabel("🔥")
        f_l = QFont()
        f_l.setPointSize(16)
        lbl_logo.setFont(f_l)
        h_brand.addWidget(lbl_logo)
        lbl_title = QLabel("BootForge")
        lbl_title.setObjectName("GroupTitle")
        f_t = QFont(self.font())
        f_t.setBold(True)
        f_t.setPointSize(13)
        lbl_title.setFont(f_t)
        h_brand.addWidget(lbl_title)
        th_lay.addLayout(h_brand)

        th_lay.addStretch()

        # Nav Tabs
        self._nav_tabs = []
        self._btn_tab_direct = QPushButton("Créer une clé")
        self._btn_tab_direct.setObjectName("NavTab")
        self._btn_tab_direct.setCheckable(True)
        self._btn_tab_direct.setChecked(True)
        self._btn_tab_direct.clicked.connect(lambda: self._switch_tab(0))
        th_lay.addWidget(self._btn_tab_direct)
        self._nav_tabs.append(self._btn_tab_direct)

        self._btn_tab_ventoy = QPushButton("Multi-systèmes")
        self._btn_tab_ventoy.setObjectName("NavTab")
        self._btn_tab_ventoy.setCheckable(True)
        self._btn_tab_ventoy.clicked.connect(lambda: self._switch_tab(1))
        th_lay.addWidget(self._btn_tab_ventoy)
        self._nav_tabs.append(self._btn_tab_ventoy)

        self._btn_tab_toolbox = QPushButton("Réparer")
        self._btn_tab_toolbox.setObjectName("NavTab")
        self._btn_tab_toolbox.setCheckable(True)
        self._btn_tab_toolbox.clicked.connect(lambda: self._switch_tab(2))
        th_lay.addWidget(self._btn_tab_toolbox)
        self._nav_tabs.append(self._btn_tab_toolbox)

        self._btn_tab_catalog = QPushButton("Télécharger")
        self._btn_tab_catalog.setObjectName("NavTab")
        self._btn_tab_catalog.setCheckable(True)
        self._btn_tab_catalog.clicked.connect(lambda: self._switch_tab(3))
        th_lay.addWidget(self._btn_tab_catalog)
        self._nav_tabs.append(self._btn_tab_catalog)

        th_lay.addStretch()

        # Mode Selector [ Simple | Avancé ]
        h_mode = QHBoxLayout()
        h_mode.setSpacing(0)
        self._btn_mode_simple = QPushButton("Simple")
        self._btn_mode_simple.setObjectName("ModeBtnLeft")
        self._btn_mode_simple.setCheckable(True)
        self._btn_mode_simple.setChecked(True)
        self._btn_mode_simple.clicked.connect(lambda: self.set_simple_mode(True))
        h_mode.addWidget(self._btn_mode_simple)

        self._btn_mode_adv = QPushButton("Avancé")
        self._btn_mode_adv.setObjectName("ModeBtnRight")
        self._btn_mode_adv.setCheckable(True)
        self._btn_mode_adv.clicked.connect(lambda: self.set_simple_mode(False))
        h_mode.addWidget(self._btn_mode_adv)
        th_lay.addLayout(h_mode)

        # Quick utility buttons
        self._btn_toggle_console = QPushButton("💻")
        self._btn_toggle_console.setToolTip("Afficher ou masquer la console")
        self._btn_toggle_console.setCheckable(True)
        self._btn_toggle_console.toggled.connect(self._toggle_console)
        th_lay.addWidget(self._btn_toggle_console)

        self._btn_theme = QPushButton("🌙" if self._dark_theme else "☀️")
        self._btn_theme.setToolTip(t("app_theme_tip"))
        self._btn_theme.clicked.connect(self._toggle_theme)
        th_lay.addWidget(self._btn_theme)

        self._btn_lang = QPushButton("FR 🇫🇷" if get_current_language() == "fr" else "EN 🇬🇧")
        self._btn_lang.setToolTip(t("app_lang_tip"))
        self._btn_lang.clicked.connect(self._toggle_language)
        th_lay.addWidget(self._btn_lang)

        btn_tools = QPushButton("⚙️")
        btn_tools.setToolTip(t("app_tools_tip"))
        btn_tools.clicked.connect(self._open_preflight)
        th_lay.addWidget(btn_tools)

        btn_about = QPushButton("ℹ️")
        btn_about.setToolTip(t("app_about_tip"))
        btn_about.clicked.connect(self._open_about)
        th_lay.addWidget(btn_about)

        main_vbox.addWidget(top_header)

        # ================= TARGET USB DEVICE BAR =================
        dev_bar = QFrame()
        dev_bar.setObjectName("DeviceBar")
        db_lay = QHBoxLayout(dev_bar)
        db_lay.setContentsMargins(16, 8, 16, 8)
        db_lay.setSpacing(10)

        lbl_usb_ic = QLabel("🔌")
        db_lay.addWidget(lbl_usb_ic)

        self._lbl_usb = QLabel(t("app_device_label"))
        self._lbl_usb.setObjectName("GroupTitle")
        db_lay.addWidget(self._lbl_usb)

        self._cmb_device = QComboBox()
        self._cmb_device.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self._cmb_device.currentIndexChanged.connect(self._on_device_selected)
        db_lay.addWidget(self._cmb_device, 1)

        self._lbl_dev_meta = QLabel()
        self._lbl_dev_meta.setObjectName("MonoLabel")
        self._lbl_dev_meta.setVisible(False)
        db_lay.addWidget(self._lbl_dev_meta)

        self._chk_show_locked = QCheckBox(f"{t('app_system_disks')} (0)")
        self._chk_show_locked.setToolTip("Afficher les disques système Windows verrouillés en lecture seule")
        self._chk_show_locked.toggled.connect(self._on_toggle_locked)
        db_lay.addWidget(self._chk_show_locked)

        self._btn_refresh_dev = QPushButton(t("app_refresh"))
        self._btn_refresh_dev.clicked.connect(self.refresh_devices)
        db_lay.addWidget(self._btn_refresh_dev)

        main_vbox.addWidget(dev_bar)

        # ================= TOAST BANNER =================
        self._toast_banner = QFrame()
        self._toast_banner.setObjectName("Card")
        self._toast_banner.setStyleSheet("background-color: #D92654; color: #FFFFFF; border: none; border-radius: 6px;")
        tb_lay = QHBoxLayout(self._toast_banner)
        tb_lay.setContentsMargins(14, 6, 14, 6)
        self._lbl_toast_msg = QLabel()
        self._lbl_toast_msg.setStyleSheet("color: #FFFFFF; font-weight: 600;")
        tb_lay.addWidget(self._lbl_toast_msg, 1)
        btn_close_toast = QPushButton("✕")
        btn_close_toast.setStyleSheet("background: transparent; border: none; color: #FFFFFF; font-weight: bold;")
        btn_close_toast.clicked.connect(lambda: self._toast_banner.setVisible(False))
        tb_lay.addWidget(btn_close_toast)
        self._toast_banner.setVisible(False)
        main_vbox.addWidget(self._toast_banner)

        # ================= STACKED VIEWS =================
        self._stack = QStackedWidget()

        self._view_direct = DirectFlashWinView(
            get_device=self.get_selected_device,
            is_busy=self._task_ctrl.is_busy,
            on_start_job=self.start_engine_job,
            on_log=self.append_log,
            on_toast=self.show_toast,
            on_layout_changed=lambda: None,
            parent=self,
        )
        self._stack.addWidget(self._view_direct)

        self._view_ventoy = MultiBootWinView(
            get_device=self.get_selected_device,
            is_busy=self._task_ctrl.is_busy,
            on_start_job=self.start_engine_job,
            on_log=self.append_log,
            on_toast=self.show_toast,
            parent=self,
        )
        self._stack.addWidget(self._view_ventoy)

        self._view_toolbox = ToolboxWinView(
            get_device=self.get_selected_device,
            task_ctrl=self._task_ctrl,
            on_start_job=self.start_engine_job,
            on_log=self.append_log,
            on_toast=self.show_toast,
            parent=self,
        )
        self._stack.addWidget(self._view_toolbox)

        self._view_catalog = CatalogWinView(
            task_ctrl=self._task_ctrl,
            on_log=self.append_log,
            on_toast=self.show_toast,
            on_use_in_direct=self._jump_to_direct_with_iso,
            on_use_in_ventoy=self._jump_to_ventoy_with_iso,
            parent=self,
        )
        self._stack.addWidget(self._view_catalog)

        main_vbox.addWidget(self._stack, 1)

        # ================= COLLAPSIBLE LOG CONSOLE =================
        self._console_wrap = QFrame()
        self._console_wrap.setObjectName("ConsoleWrap")
        cw_lay = QVBoxLayout(self._console_wrap)
        cw_lay.setContentsMargins(14, 6, 14, 8)
        cw_lay.setSpacing(6)

        c_top = QHBoxLayout()
        self._lbl_console_title = QLabel(f"<b>{t('bar_journal_title')}</b>")
        c_top.addWidget(self._lbl_console_title)
        c_top.addStretch()
        self._btn_copy_log = QPushButton(t("bar_journal_copy"))
        self._btn_copy_log.clicked.connect(self._copy_logs)
        c_top.addWidget(self._btn_copy_log)
        self._btn_clear_log = QPushButton(t("bar_journal_clear"))
        self._btn_clear_log.clicked.connect(self._clear_logs)
        c_top.addWidget(self._btn_clear_log)
        btn_hide_log = QPushButton("✕")
        btn_hide_log.clicked.connect(lambda: self._btn_toggle_console.setChecked(False))
        c_top.addWidget(btn_hide_log)
        cw_lay.addLayout(c_top)

        self._txt_console = QPlainTextEdit()
        self._txt_console.setObjectName("ConsoleText")
        self._txt_console.setReadOnly(True)
        self._txt_console.setFixedHeight(140)
        cw_lay.addWidget(self._txt_console)

        self._console_wrap.setVisible(False)
        main_vbox.addWidget(self._console_wrap)

        # ================= BOTTOM STATUS BAR =================
        status_bar = QFrame()
        status_bar.setObjectName("StatusBar")
        sb_lay = QHBoxLayout(status_bar)
        sb_lay.setContentsMargins(16, 7, 16, 7)
        sb_lay.setSpacing(12)

        self._lbl_status_badge = QLabel(t("status_badge_ready"))
        self._lbl_status_badge.setObjectName("MonoLabel")
        sb_lay.addWidget(self._lbl_status_badge)

        self._lbl_status_msg = QLabel(t("status_ready"))
        sb_lay.addWidget(self._lbl_status_msg, 1)

        self._lbl_speed = QLabel()
        self._lbl_speed.setObjectName("MonoLabel")
        self._lbl_speed.setVisible(False)
        sb_lay.addWidget(self._lbl_speed)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(190)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        sb_lay.addWidget(self._progress_bar)

        self._lbl_pct = QLabel("0 %")
        self._lbl_pct.setObjectName("MonoLabel")
        sb_lay.addWidget(self._lbl_pct)

        self._btn_cancel = QPushButton(t("bar_btn_cancel"))
        self._btn_cancel.setObjectName("DangerBtn")
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_job)
        sb_lay.addWidget(self._btn_cancel)

        self._btn_log_count = QPushButton(t("bar_journal_btn", count=0))
        self._btn_log_count.clicked.connect(lambda: self._btn_toggle_console.setChecked(not self._btn_toggle_console.isChecked()))
        sb_lay.addWidget(self._btn_log_count)

        main_vbox.addWidget(status_bar)

    def apply_theme(self) -> None:
        self.setStyleSheet(build_qss(self._dark_theme))
        self._btn_theme.setText("🌙" if self._dark_theme else "☀️")
        self._view_direct._part_map.set_dark(self._dark_theme)
        self._view_ventoy._part_map.set_dark(self._dark_theme)

    def _toggle_theme(self) -> None:
        self._dark_theme = not self._dark_theme
        save_win_prefs({"theme": "dark" if self._dark_theme else "light"})
        self.apply_theme()

    def _toggle_language(self) -> None:
        cur = get_current_language()
        nxt = "en" if cur == "fr" else "fr"
        set_current_language(nxt)
        self.apply_language()

    def apply_language(self) -> None:
        lang = get_current_language()
        self._btn_lang.setText("FR 🇫🇷" if lang == "fr" else "EN 🇬🇧")
        self._btn_lang.setToolTip(t("app_lang_tip"))
        self._btn_toggle_console.setToolTip(t("app_console_tip"))
        self._btn_theme.setToolTip(t("app_theme_tip"))
        self._lbl_console_title.setText(f"<b>{t('bar_journal_title')}</b>")
        self._btn_copy_log.setText(t("bar_journal_copy"))
        self._btn_clear_log.setText(t("bar_journal_clear"))
        self._btn_cancel.setText(t("bar_btn_cancel"))
        self._btn_log_count.setText(t("bar_journal_btn", count=self._log_count))
        if not self._task_ctrl.is_busy():
            self._lbl_status_badge.setText(t("status_badge_ready"))
            self._lbl_status_msg.setText(t("status_ready"))
        self._view_direct.apply_language()
        self._view_ventoy.apply_language()
        self._view_toolbox.apply_language()
        self._view_catalog.apply_language()
        self.set_simple_mode(self._simple_mode, persist=False)

    def set_simple_mode(self, simple: bool, persist: bool = True) -> None:
        self._simple_mode = simple
        self._btn_mode_simple.setChecked(simple)
        self._btn_mode_adv.setChecked(not simple)

        if persist:
            save_win_prefs({"mode": "simple" if simple else "advanced"})

        if simple:
            self._btn_tab_direct.setText(t("nav_direct_simple"))
            self._btn_tab_ventoy.setText(t("nav_ventoy_simple"))
            self._btn_tab_toolbox.setText(t("nav_toolbox_simple"))
            self._btn_tab_catalog.setText(t("nav_catalog_simple"))
            self._lbl_usb.setText(t("app_device_label"))
            self._chk_show_locked.setVisible(False)
            self._btn_toggle_console.setVisible(False)
            self._btn_log_count.setVisible(False)
            self._lbl_status_badge.setVisible(False)
            self._progress_bar.setVisible(self._task_ctrl.is_busy())
            self._lbl_pct.setVisible(self._task_ctrl.is_busy())
        else:
            self._btn_tab_direct.setText(t("nav_direct_adv"))
            self._btn_tab_ventoy.setText(t("nav_ventoy_adv"))
            self._btn_tab_toolbox.setText(t("nav_toolbox_adv"))
            self._btn_tab_catalog.setText(t("nav_catalog_adv"))
            self._lbl_usb.setText(t("app_device_target"))
            self._chk_show_locked.setVisible(True)
            self._btn_toggle_console.setVisible(True)
            self._btn_log_count.setVisible(True)
            self._lbl_status_badge.setVisible(True)
            self._progress_bar.setVisible(True)
            self._lbl_pct.setVisible(True)

        self._btn_mode_simple.setText(t("mode_simple"))
        self._btn_mode_adv.setText(t("mode_advanced"))
        self._btn_mode_simple.setToolTip(t("mode_simple_tip"))
        self._btn_mode_adv.setToolTip(t("mode_advanced_tip"))
        self._btn_refresh_dev.setText(t("app_refresh"))

        self._view_direct.set_simple_mode(simple)
        self._view_ventoy.set_simple_mode(simple)
        self._view_toolbox.set_simple_mode(simple)
        self._view_catalog.set_simple_mode(simple)
        self._rebuild_device_dropdown()

    def _switch_tab(self, idx: int) -> None:
        for i, b in enumerate(self._nav_tabs):
            b.setChecked(i == idx)
        self._stack.setCurrentIndex(idx)

    def _toggle_console(self, checked: bool) -> None:
        self._console_wrap.setVisible(checked)

    def refresh_devices(self) -> None:
        def _ok(devs: List[Dict[str, Any]]) -> None:
            self._devices = devs
            self._rebuild_device_dropdown()

        def _err(msg: str) -> None:
            self.append_log("error", f"Erreur inventaire disques : {msg}")

        self._async_helper.run_fn(list_block_devices, _ok, _err)

    def _on_toggle_locked(self, checked: bool) -> None:
        self._show_locked = checked
        self._rebuild_device_dropdown()

    def _rebuild_device_dropdown(self) -> None:
        self._syncing_dropdown = True
        try:
            self._cmb_device.clear()
            sys_cnt = sum(1 for d in self._devices if d.get("is_system_disk"))
            self._chk_show_locked.setText(f"{t('app_system_disks')} ({sys_cnt})")

            visible = (
                list(self._devices)
                if (self._show_locked and not self._simple_mode)
                else [d for d in self._devices if not d.get("is_system_disk")]
            )
            self._visible_devices = visible

            if not visible:
                self._cmb_device.addItem("Branchez une clé USB puis cliquez sur Actualiser")
                self._selected_device_path = ""
            else:
                for d in visible:
                    sz_txt = format_bytes(d.get("size_bytes", 0))
                    vtoy = " (Multi-systèmes)" if d.get("ventoy_detected") else ""
                    if self._simple_mode:
                        self._cmb_device.addItem(f"{d.get('model', 'Clé USB')} — {sz_txt}{vtoy}")
                    else:
                        lock = " [SYSTÈME VERROUILLÉ]" if d.get("is_system_disk") else ""
                        self._cmb_device.addItem(f"{d.get('model', 'Disque')} — {sz_txt} ({d.get('path', '')}){vtoy}{lock}")

                # Restore selection
                match_idx = next((i for i, d in enumerate(visible) if d["path"] == self._selected_device_path), None)
                if match_idx is not None:
                    self._cmb_device.setCurrentIndex(match_idx)
                else:
                    self._cmb_device.setCurrentIndex(0)
                    self._selected_device_path = visible[0]["path"]
        finally:
            self._syncing_dropdown = False

        self._sync_selected_device()

    def _on_device_selected(self, idx: int) -> None:
        if self._syncing_dropdown or idx < 0 or idx >= len(self._visible_devices):
            return
        self._selected_device_path = self._visible_devices[idx]["path"]
        self._sync_selected_device()

    def get_selected_device(self) -> Optional[Dict[str, Any]]:
        if not self._selected_device_path:
            return None
        return next((d for d in self._devices if d["path"] == self._selected_device_path), None)

    def _sync_selected_device(self) -> None:
        dev = self.get_selected_device()
        if not dev or self._simple_mode:
            self._lbl_dev_meta.setVisible(False)
        else:
            self._lbl_dev_meta.setText(" · ".join(filter(None, [dev.get("serial"), dev.get("transport", "").upper(), "VTOYEFI" if dev.get("ventoy_detected") else ""])))
            self._lbl_dev_meta.setVisible(True)

        if not self._simple_mode:
            self._view_direct.refresh_partition_map()
        self._view_direct.sync_sensitivity()
        self._view_ventoy.on_device_changed()
        self._view_toolbox.sync_sensitivity()

    def start_engine_job(self, job: Dict[str, Any], dry_run: bool) -> None:
        if self._task_ctrl.is_busy():
            self.show_toast("Une opération est déjà en cours.")
            return

        self._progress_bar.setVisible(True)
        self._lbl_pct.setVisible(True)
        self._progress_bar.setValue(2)
        self._lbl_pct.setText("2 %")
        self._lbl_status_badge.setText("SIMULATION" if dry_run else "EN COURS")
        self._lbl_status_msg.setText("Préparation de l'opération...")
        self._btn_cancel.setVisible(True)

        self._task_ctrl.run_engine_job(job, dry_run)

    def _cancel_job(self) -> None:
        self._task_ctrl.cancel()
        self.append_log("warn", "Annulation demandée par l'utilisateur.")
        self._lbl_status_badge.setText("ANNULÉ")
        self._lbl_status_msg.setText("Opération annulée.")

    def _handle_telemetry(self, ev: Dict[str, Any]) -> None:
        etype = ev.get("type")
        if etype == "progress":
            pct = float(ev.get("percent") or 0.0)
            self._progress_bar.setValue(int(pct))
            self._lbl_pct.setText(f"{pct:.0f} %")
            if ev.get("message"):
                self._lbl_status_msg.setText(str(ev["message"]))
            spd = ev.get("speed_bps")
            if spd and not self._simple_mode:
                self._lbl_speed.setText(format_speed(float(spd)))
                self._lbl_speed.setVisible(True)
            else:
                self._lbl_speed.setVisible(False)
        elif etype == "log":
            self.append_log(str(ev.get("level") or "info"), str(ev.get("message") or ""))
        elif etype == "error":
            msg = str(ev.get("message") or "Erreur")
            self.append_log("error", msg)
            self._lbl_status_msg.setText(msg)
            self._lbl_status_badge.setText("ERREUR")
            self.show_toast(msg)
        elif etype == "benchmark_result":
            mbps = float(ev.get("read_mbps") or 0.0)
            self._view_toolbox.set_benchmark_result(mbps)
            self.append_log("info", f"Débit de lecture mesuré : {mbps:.2f} Mo/s")

    def _handle_job_done(self, ok: bool, exit_code: int, err_msg: str) -> None:
        self._btn_cancel.setVisible(False)
        self._lbl_speed.setVisible(False)
        if ok:
            self._progress_bar.setValue(100)
            self._lbl_pct.setText("100 %")
            self._lbl_status_badge.setText("TERMINÉ")
            self.show_toast("Opération terminée avec succès !")
        else:
            self._lbl_status_badge.setText("ÉCHEC")
            self.show_toast(f"Échec de l'opération : {err_msg or f'Code {exit_code}'}")
        self.refresh_devices()

    def _handle_busy_changed(self, busy: bool) -> None:
        self._btn_cancel.setVisible(busy)
        if self._simple_mode:
            self._progress_bar.setVisible(busy)
            self._lbl_pct.setVisible(busy)
        self._view_direct.sync_sensitivity()
        self._view_ventoy.sync_sensitivity()
        self._view_toolbox.sync_sensitivity()
        self._view_catalog.sync_sensitivity()

    def append_log(self, level: str, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{level.upper():7s}] {message}"
        self._txt_console.appendPlainText(line)
        self._log_count += 1
        self._btn_log_count.setText(f"Journal ({self._log_count})")

    def _copy_logs(self) -> None:
        QApplication.clipboard().setText(self._txt_console.toPlainText())
        self.show_toast("Journal copié.")

    def _clear_logs(self) -> None:
        self._txt_console.clear()
        self._log_count = 0
        self._btn_log_count.setText("Journal (0)")

    def show_toast(self, message: str) -> None:
        self._lbl_toast_msg.setText(message)
        self._toast_banner.setVisible(True)
        QTimer.singleShot(4500, lambda: self._toast_banner.setVisible(False))

    def _jump_to_direct_with_iso(self, path: str) -> None:
        self._view_direct.load_iso_external(path)
        self._switch_tab(0)

    def _jump_to_ventoy_with_iso(self, path: str) -> None:
        self._view_ventoy.add_iso_external(path)
        self._switch_tab(1)

    def _open_preflight(self) -> None:
        dlg = PreflightDialog(deps=None, on_refresh=self.refresh_devices, parent=self)
        dlg.exec()

    def _open_about(self) -> None:
        dlg = AboutDialog(parent=self)
        dlg.exec()


def run_windows_app(argv: Optional[List[str]] = None) -> int:
    """Entry point for native Windows PyQt6 application."""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("BootForge")
    app.setOrganizationName("NiceMank")

    ico_path = RESOURCE_ROOT / "assets" / "icon.ico"
    if ico_path.is_file():
        app.setWindowIcon(QIcon(str(ico_path)))

    init_iso = ""
    args = argv if argv is not None else sys.argv
    if len(args) > 1 and not args[1].startswith("-") and os.path.isfile(args[1]):
        init_iso = args[1]

    win = BootForgeMainWindow(initial_iso=init_iso)
    win.show()
    return app.exec()
