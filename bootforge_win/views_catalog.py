"""Official OS download catalog view for BootForge Windows PyQt6 UI."""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bootforge_win.backend import WinTaskController
from engine.catalog import OFFICIAL_CATALOG, default_downloads_dir, get_localized_catalog
from engine.i18n import get_current_language, t


class CatalogWinView(QWidget):
    """Catalog of verified OS images with direct HTTPS download and 1-click flash integration."""

    def __init__(
        self,
        task_ctrl: WinTaskController,
        on_log: Callable[[str, str], None],
        on_toast: Callable[[str], None],
        on_use_in_direct: Callable[[str], None],
        on_use_in_ventoy: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctrl = task_ctrl
        self._on_log = on_log
        self._on_toast = on_toast
        self._on_use_in_direct = on_use_in_direct
        self._on_use_in_ventoy = on_use_in_ventoy

        self._simple_mode = True
        self._dest_dir = str(default_downloads_dir())
        self._active_category_idx = 0
        self._downloaded_paths: Dict[str, str] = {}
        self._dl_buttons: List[QPushButton] = []

        self._build_ui()
        self.set_simple_mode(True)
        self._ctrl.download_done.connect(self._on_download_done)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        # Top Bar: Categories & Destination Folder
        top_bar = QHBoxLayout()
        self._cat_container = QWidget()
        self._cat_layout = QHBoxLayout(self._cat_container)
        self._cat_layout.setContentsMargins(0, 0, 0, 0)
        self._cat_layout.setSpacing(6)
        self._cat_btn_group = QButtonGroup(self)
        top_bar.addWidget(self._cat_container)

        top_bar.addStretch()

        self._lbl_dest = QLabel(t("catalog_folder_lbl", path=self._dest_dir))
        self._lbl_dest.setObjectName("Muted")
        top_bar.addWidget(self._lbl_dest)

        self._btn_pick_folder = QPushButton(t("catalog_folder_btn"))
        self._btn_pick_folder.clicked.connect(self._browse_dest_folder)
        top_bar.addWidget(self._btn_pick_folder)
        main_layout.addLayout(top_bar)

        # Scroll items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._scroll_widget = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_widget)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(10)

        scroll.setWidget(self._scroll_widget)
        main_layout.addWidget(scroll, 1)

    def apply_language(self) -> None:
        """Refresh localized text and catalog strings."""
        self._lbl_dest.setText(t("catalog_folder_lbl", path=self._dest_dir))
        self._btn_pick_folder.setText(t("catalog_folder_btn"))
        self._rebuild_category_buttons()
        self._rebuild_rows()

    def set_simple_mode(self, simple: bool) -> None:
        self._simple_mode = simple
        self._active_category_idx = 0
        self._rebuild_category_buttons()
        self._rebuild_rows()

    def _get_category_labels(self) -> List[str]:
        if self._simple_mode:
            return [
                t("catalog_cat_all_simple"),
                t("catalog_cat_current_simple"),
                t("catalog_cat_rescue_simple"),
                t("catalog_cat_security_simple"),
            ]
        return [
            t("catalog_cat_all_adv"),
            t("catalog_cat_desktop_adv"),
            t("catalog_cat_security_adv"),
            t("catalog_cat_rescue_adv"),
        ]

    def _rebuild_category_buttons(self) -> None:
        # Clear existing buttons
        while self._cat_layout.count():
            item = self._cat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        cats = self._get_category_labels()
        for idx, c in enumerate(cats):
            btn = QPushButton(c)
            btn.setCheckable(True)
            self._cat_btn_group.addButton(btn)
            if idx == self._active_category_idx:
                btn.setChecked(True)
            btn.toggled.connect(lambda checked, i=idx: self._on_cat_toggled(checked, i))
            self._cat_layout.addWidget(btn)

    def _on_cat_toggled(self, checked: bool, index: int) -> None:
        if checked:
            self._active_category_idx = index
            self._rebuild_rows()

    def _browse_dest_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("catalog_dest_folder"), self._dest_dir)
        if d:
            self._dest_dir = d
            self._lbl_dest.setText(t("catalog_folder_lbl", path=d))

    def _rebuild_rows(self) -> None:
        # Clear items
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._dl_buttons.clear()

        cur_lang = get_current_language()
        all_catalog = get_localized_catalog(cur_lang)
        cat_labels = self._get_category_labels()
        target_cat = cat_labels[self._active_category_idx] if self._active_category_idx < len(cat_labels) else cat_labels[0]
        cat_key = "simple_category" if self._simple_mode else "category"

        items = [
            it
            for it in all_catalog
            if self._active_category_idx == 0 or it.get(cat_key) == target_cat
        ]

        for item in items:
            frame = QFrame()
            frame.setObjectName("Card")
            f_lay = QVBoxLayout(frame)
            f_lay.setContentsMargins(14, 12, 14, 12)
            f_lay.setSpacing(6)

            h_head = QHBoxLayout()
            title = item["name"] if self._simple_mode else f"{item['name']} · {item['version']}"
            lbl_title = QLabel(f"<b>{title}</b>")
            lbl_title.setObjectName("GroupTitle")
            h_head.addWidget(lbl_title, 1)

            size_badge = QLabel(item.get("size_hint", ""))
            size_badge.setObjectName("MonoLabel")
            h_head.addWidget(size_badge)
            f_lay.addLayout(h_head)

            desc = item.get("simple_description") if self._simple_mode else item.get("description")
            lbl_desc = QLabel(str(desc or ""))
            lbl_desc.setWordWrap(True)
            f_lay.addWidget(lbl_desc)

            h_bot = QHBoxLayout()
            if not self._simple_mode:
                btn_cs = QPushButton(t("catalog_btn_checksum"))
                btn_cs.clicked.connect(lambda _, u=item["checksum_page"]: webbrowser.open(u))
                h_bot.addWidget(btn_cs)

            h_bot.addStretch()

            saved_path = self._downloaded_paths.get(item["id"])
            if saved_path and os.path.isfile(saved_path):
                btn_use = QPushButton(t("catalog_btn_use_simple") if self._simple_mode else t("catalog_btn_use_adv"))
                btn_use.clicked.connect(lambda _, p=saved_path: self._on_use_in_direct(p))
                h_bot.addWidget(btn_use)

                btn_vtoy = QPushButton(t("catalog_btn_ventoy_simple") if self._simple_mode else t("catalog_btn_ventoy_adv"))
                btn_vtoy.setObjectName("PrimaryBtn")
                btn_vtoy.clicked.connect(lambda _, p=saved_path: self._on_use_in_ventoy(p))
                h_bot.addWidget(btn_vtoy)
            else:
                btn_dl = QPushButton(t("catalog_btn_dl_simple") if self._simple_mode else t("catalog_btn_dl_adv"))
                btn_dl.setObjectName("PrimaryBtn")
                btn_dl.setEnabled(not self._ctrl.is_busy())
                btn_dl.clicked.connect(lambda _, it=item: self._start_download(it))
                h_bot.addWidget(btn_dl)
                self._dl_buttons.append(btn_dl)

            f_lay.addLayout(h_bot)
            self._scroll_layout.addWidget(frame)

        self._scroll_layout.addStretch()

    def sync_sensitivity(self) -> None:
        busy = self._ctrl.is_busy()
        for b in self._dl_buttons:
            b.setEnabled(not busy)

    def _start_download(self, item: Dict[str, str]) -> None:
        if self._ctrl.is_busy():
            self._on_toast("Une opération est déjà en cours.")
            return

        self._on_log("info", f"Téléchargement officiel : {item.get('name')} ({item.get('url')})")
        self.sync_sensitivity()
        self._ctrl.run_catalog_download(item, self._dest_dir)

    def _on_download_done(self, ok: bool, path_or_err: str) -> None:
        self.sync_sensitivity()
        if ok:
            name = Path(path_or_err).name
            self._on_toast(f"Téléchargement terminé : {name}")
            # Identify item id from name
            for it in OFFICIAL_CATALOG:
                if it.get("url", "").endswith(name) or it.get("id") in name:
                    self._downloaded_paths[it["id"]] = path_or_err
            self._rebuild_rows()
        else:
            self._on_toast(f"Échec du téléchargement : {path_or_err}")
