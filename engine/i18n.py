"""Internationalization (i18n) module for BootForge.

Provides French and English localization, system language auto-detection,
persistent preference storage, and reactive language switching across
Linux (GTK4/Libadwaita) and Windows (PyQt6) editions.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SUPPORTED_LANGUAGES = {
    "fr": "Français",
    "en": "English",
}

DEFAULT_LANGUAGE = "fr"

_current_language = DEFAULT_LANGUAGE
_subscribers: List[Callable[[str], None]] = []


def _get_settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "bootforge" / "settings.json"


def detect_system_language() -> str:
    """Detect OS display language, defaulting to 'fr' if French or 'en' for others."""
    # 1. Environment variables (Linux / POSIX / overridden)
    for env_var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_var, "")
        if val:
            val = val.lower()
            if val.startswith("fr") or "french" in val:
                return "fr"
            if val.startswith("en") or "english" in val:
                return "en"

    # 2. Windows API if running on win32
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF
            # Primary language ID: 0x0C = French, 0x09 = English
            if lang_id == 0x0C:
                return "fr"
            return "en"
        except Exception:
            pass

    return "fr"


def init_language() -> str:
    """Load language from user settings or auto-detect."""
    global _current_language
    path = _get_settings_path()
    saved_lang = None
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                saved_lang = data.get("language")
    except Exception:
        pass

    if saved_lang in SUPPORTED_LANGUAGES:
        _current_language = saved_lang
    else:
        _current_language = detect_system_language()

    return _current_language


def get_current_language() -> str:
    return _current_language


def set_current_language(lang: str, persist: bool = True) -> None:
    global _current_language
    if lang not in SUPPORTED_LANGUAGES:
        return
    if _current_language == lang and not persist:
        return
    _current_language = lang

    if persist:
        path = _get_settings_path()
        try:
            data = {}
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
            data["language"] = lang
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # Notify all subscribed views/windows
    for sub in list(_subscribers):
        try:
            sub(lang)
        except Exception:
            pass


def subscribe_language_changed(cb: Callable[[str], None]) -> None:
    if cb not in _subscribers:
        _subscribers.append(cb)


def unsubscribe_language_changed(cb: Callable[[str], None]) -> None:
    if cb in _subscribers:
        _subscribers.remove(cb)


# ================= DICTIONARY =================

STRINGS: Dict[str, Dict[str, str]] = {
    # Navigation Tabs
    "nav_direct_simple": {
        "fr": "Créer une clé",
        "en": "Create USB",
    },
    "nav_direct_adv": {
        "fr": "Flash direct",
        "en": "Direct Flash",
    },
    "nav_ventoy_simple": {
        "fr": "Multi-systèmes",
        "en": "Multi-OS",
    },
    "nav_ventoy_adv": {
        "fr": "Multi-Boot",
        "en": "Multi-Boot",
    },
    "nav_toolbox_simple": {
        "fr": "Réparer",
        "en": "Repair & Tools",
    },
    "nav_toolbox_adv": {
        "fr": "Outils USB",
        "en": "USB Tools",
    },
    "nav_catalog_simple": {
        "fr": "Télécharger",
        "en": "Download OS",
    },
    "nav_catalog_adv": {
        "fr": "Catalogue ISO",
        "en": "OS Catalog",
    },

    # Modes
    "mode_simple": {
        "fr": "Simple",
        "en": "Simple",
    },
    "mode_advanced": {
        "fr": "Avancé",
        "en": "Advanced",
    },
    "mode_simple_tip": {
        "fr": "Interface guidée sans réglages techniques",
        "en": "Guided interface without technical jargon",
    },
    "mode_advanced_tip": {
        "fr": "Affiche toutes les options Rufus, Ventoy, partitions et console",
        "en": "Shows full Rufus, Ventoy, partition layout and console options",
    },

    # Top & Target bar
    "app_device_label": {
        "fr": "Clé USB :",
        "en": "USB Drive:",
    },
    "app_device_target": {
        "fr": "Périphérique cible :",
        "en": "Target device:",
    },
    "app_refresh": {
        "fr": "Actualiser",
        "en": "Refresh",
    },
    "app_system_disks": {
        "fr": "Disques système",
        "en": "System drives",
    },
    "app_theme_tip": {
        "fr": "Basculer thème Sombre / Clair",
        "en": "Toggle Dark / Light theme",
    },
    "app_console_tip": {
        "fr": "Afficher ou masquer le journal système",
        "en": "Toggle system execution log",
    },
    "app_tools_tip": {
        "fr": "Outils système installés...",
        "en": "Installed system tools...",
    },
    "app_about_tip": {
        "fr": "À propos de BootForge",
        "en": "About BootForge",
    },
    "app_lang_tip": {
        "fr": "Changer la langue (Français / English)",
        "en": "Switch language (Français / English)",
    },
    "app_ready": {
        "fr": "Prêt",
        "en": "Ready",
    },
    "app_busy": {
        "fr": "Opération en cours...",
        "en": "Operation in progress...",
    },
    "app_success": {
        "fr": "Opération terminée avec succès.",
        "en": "Operation completed successfully.",
    },
    "app_error": {
        "fr": "Erreur lors de l'opération.",
        "en": "Error during operation.",
    },
    "app_no_device": {
        "fr": "Aucune clé USB détectée. Branchez une clé USB pour commencer.",
        "en": "No USB drive detected. Insert a USB drive to begin.",
    },

    # Direct / Rufus Flash
    "direct_s_step1_title": {
        "fr": "1. Choisissez votre système",
        "en": "1. Choose your operating system",
    },
    "direct_s_step1_desc": {
        "fr": "Sélectionnez le fichier .iso ou .img à installer sur votre clé.",
        "en": "Select the .iso or .img file to write to your USB drive.",
    },
    "direct_s_step2_title": {
        "fr": "2. Options pratiques",
        "en": "2. Practical options",
    },
    "direct_s_step2_desc": {
        "fr": "Ajustements automatiques pour votre installation.",
        "en": "Automatic tweaks for your installation.",
    },
    "direct_s_step3_title": {
        "fr": "3. Prêt à créer la clé",
        "en": "3. Ready to create USB",
    },
    "direct_s_step3_desc": {
        "fr": "Toutes les données de la clé sélectionnée seront effacées.",
        "en": "All data on the selected USB drive will be erased.",
    },
    "direct_adv_group_src": {
        "fr": "Image système et schéma de partition",
        "en": "Disk or ISO image & partition scheme",
    },
    "direct_adv_group_tgt": {
        "fr": "Système de fichiers et formatage",
        "en": "File system & formatting",
    },
    "direct_drop_title": {
        "fr": "Glissez-déposez une image .iso ou .img ici\nou cliquez pour parcourir vos dossiers",
        "en": "Drag and drop an .iso or .img image here\nor click to browse your files",
    },
    "direct_browse_btn": {
        "fr": "Choisir un fichier",
        "en": "Browse file",
    },
    "direct_browse_btn_adv": {
        "fr": "Parcourir...",
        "en": "Browse...",
    },
    "direct_win11_tweak_chk": {
        "fr": "Faciliter l'installation de Windows 11 (sans compte Microsoft ni exigences TPM)",
        "en": "Facilitate Windows 11 setup (bypass TPM, Secure Boot & Microsoft Account)",
    },
    "direct_win11_adv_btn": {
        "fr": "Personnaliser Windows 11 (Tweaks)...",
        "en": "Customize Windows 11 (Tweaks)...",
    },
    "direct_persistence_label": {
        "fr": "Espace de persistance Linux (Go) :",
        "en": "Linux persistence storage (GB):",
    },
    "direct_scheme_label": {
        "fr": "Schéma de partition :",
        "en": "Partition scheme:",
    },
    "direct_scheme_gpt": {
        "fr": "GPT (UEFI moderne)",
        "en": "GPT (Modern UEFI)",
    },
    "direct_scheme_mbr": {
        "fr": "MBR (BIOS hérité / CSM)",
        "en": "MBR (Legacy BIOS / CSM)",
    },
    "direct_target_label": {
        "fr": "Système cible :",
        "en": "Target system:",
    },
    "direct_target_uefi": {
        "fr": "UEFI (non CSM)",
        "en": "UEFI (non CSM)",
    },
    "direct_target_bios": {
        "fr": "BIOS ou UEFI-CSM",
        "en": "BIOS or UEFI-CSM",
    },
    "direct_fs_label": {
        "fr": "Système de fichiers :",
        "en": "File system:",
    },
    "direct_fs_ntfs": {
        "fr": "NTFS + UEFI:NTFS",
        "en": "NTFS + UEFI:NTFS",
    },
    "direct_fs_fat32": {
        "fr": "FAT32 (par défaut)",
        "en": "FAT32 (default)",
    },
    "direct_fs_exfat": {
        "fr": "exFAT",
        "en": "exFAT",
    },
    "direct_fs_dd": {
        "fr": "Image brute (DD)",
        "en": "Raw image (DD)",
    },
    "direct_volume_label": {
        "fr": "Nom de volume :",
        "en": "Volume label:",
    },
    "direct_cluster_label": {
        "fr": "Taille d'unité d'allocation :",
        "en": "Cluster size:",
    },
    "direct_cluster_default": {
        "fr": "Défaut (4096 octets)",
        "en": "Default (4096 bytes)",
    },
    "direct_btn_start": {
        "fr": "Démarrer",
        "en": "Start",
    },
    "direct_btn_dry": {
        "fr": "Simuler (dry-run)",
        "en": "Simulate (dry-run)",
    },
    "direct_part_map_title": {
        "fr": "Schéma prévisionnel des partitions",
        "en": "Partition layout preview",
    },

    # Ventoy / Multi-Boot
    "ventoy_s_step1_title": {
        "fr": "1. Que voulez-vous faire sur la clé ?",
        "en": "1. What do you want to do on the USB drive?",
    },
    "ventoy_s_step1_desc": {
        "fr": "Placez plusieurs systèmes (Windows, Ubuntu, etc.) sur la même clé USB.",
        "en": "Put multiple operating systems (Windows, Ubuntu, etc.) on the same USB drive.",
    },
    "ventoy_s_opt_fresh": {
        "fr": "Préparer une clé multi-systèmes de zéro (efface la clé)",
        "en": "Prepare multi-boot USB from scratch (erases drive)",
    },
    "ventoy_s_opt_sync": {
        "fr": "Ajouter ou retirer des systèmes sans tout effacer",
        "en": "Add or remove systems without erasing drive",
    },
    "ventoy_s_step2_title": {
        "fr": "2. Option pratique",
        "en": "2. Practical option",
    },
    "ventoy_s_win11_chk": {
        "fr": "Faciliter l'installation de Windows 11 (contournement TPM / Secure Boot)",
        "en": "Facilitate Windows 11 setup (bypass TPM / Secure Boot)",
    },
    "ventoy_s_step3_title": {
        "fr": "3. Systèmes à mettre sur la clé",
        "en": "3. Systems to put on the USB drive",
    },
    "ventoy_s_step3_desc": {
        "fr": "Ajoutez un ou plusieurs fichiers .iso (Windows, Ubuntu, Mint...).",
        "en": "Add one or more .iso files (Windows, Ubuntu, Mint...).",
    },
    "ventoy_adv_act_title": {
        "fr": "Opération Ventoy",
        "en": "Ventoy Operation",
    },
    "ventoy_adv_act_desc": {
        "fr": "Installe le chargeur multi-OS ou synchronise vos images ISO.",
        "en": "Installs multi-OS bootloader or synchronizes ISO images.",
    },
    "ventoy_adv_opt_install": {
        "fr": "Installer Ventoy (initialisation complète du disque)",
        "en": "Install Ventoy (full disk initialization)",
    },
    "ventoy_adv_opt_apply": {
        "fr": "Synchroniser les ISO et ventoy.json (sans formater)",
        "en": "Synchronize ISOs and ventoy.json (without formatting)",
    },
    "ventoy_adv_opt_update": {
        "fr": "Mettre à jour le chargeur Ventoy uniquement (sans toucher aux ISO)",
        "en": "Update Ventoy bootloader only (leave ISOs untouched)",
    },
    "ventoy_card_cont_title": {
        "fr": "Paramètres du conteneur Ventoy",
        "en": "Ventoy Container Settings",
    },
    "ventoy_secureboot_chk": {
        "fr": "Prise en charge Secure Boot (VTOYEFI)",
        "en": "Secure Boot support (VTOYEFI)",
    },
    "ventoy_volume_label": {
        "fr": "Nom du volume (exFAT) :",
        "en": "Volume label (exFAT):",
    },
    "ventoy_reserve_label": {
        "fr": "Espace réservé en fin de disque (Mo) :",
        "en": "Reserved space at end of disk (MB):",
    },
    "ventoy_rules_title": {
        "fr": "Règles globales (/ventoy/ventoy.json)",
        "en": "Global rules (/ventoy/ventoy.json)",
    },
    "ventoy_win11_nro_chk": {
        "fr": "Autoriser un compte local Windows 11 (VTOY_WIN11_BYPASS_NRO)",
        "en": "Allow local account on Windows 11 (VTOY_WIN11_BYPASS_NRO)",
    },
    "ventoy_tree_chk": {
        "fr": "Afficher le menu de démarrage en arborescence",
        "en": "Display boot menu as a directory tree",
    },
    "ventoy_filter_chk": {
        "fr": "Masquer les fichiers cachés (._*)",
        "en": "Hide hidden files (._*)",
    },
    "ventoy_timeout_label": {
        "fr": "Délai de démarrage automatique (sec) :",
        "en": "Auto-boot timeout (sec):",
    },
    "ventoy_queue_title": {
        "fr": "File Multi-Boot et réglages par système",
        "en": "Multi-Boot Queue & Per-System Settings",
    },
    "ventoy_queue_desc": {
        "fr": "Gérez les images, l'alias de menu et les profils de démarrage.",
        "en": "Manage images, menu aliases and boot profiles.",
    },
    "ventoy_btn_read": {
        "fr": "Lire la clé",
        "en": "Read USB drive",
    },
    "ventoy_btn_json": {
        "fr": "Aperçu JSON",
        "en": "JSON Preview",
    },
    "ventoy_btn_add": {
        "fr": "+ Ajouter un fichier",
        "en": "+ Add a file",
    },
    "ventoy_btn_add_adv": {
        "fr": "+ Ajouter ISO",
        "en": "+ Add ISO",
    },
    "ventoy_empty_title": {
        "fr": "Aucun système ajouté pour l'instant.\nCliquez sur « + Ajouter un fichier » pour choisir vos images ISO.",
        "en": "No system added yet.\nClick '+ Add a file' to choose your ISO images.",
    },
    "ventoy_item_on_usb": {
        "fr": "Déjà sur la clé",
        "en": "On USB drive",
    },
    "ventoy_item_queued": {
        "fr": "À copier",
        "en": "To copy",
    },
    "ventoy_item_remove": {
        "fr": "Retirer",
        "en": "Remove",
    },
    "ventoy_item_alias": {
        "fr": "Nom au menu :",
        "en": "Menu alias:",
    },
    "ventoy_item_alias_ph": {
        "fr": "Nom personnalisé dans le menu de démarrage...",
        "en": "Custom name in boot menu...",
    },
    "ventoy_item_default": {
        "fr": "Démarrer par défaut sur ce système",
        "en": "Boot this system by default",
    },
    "ventoy_btn_run_simple": {
        "fr": "Préparer la clé",
        "en": "Prepare USB drive",
    },
    "ventoy_btn_run_adv": {
        "fr": "Exécuter Ventoy",
        "en": "Execute Ventoy",
    },

    # Toolbox
    "toolbox_hash_title": {
        "fr": "Vérifier l'intégrité d'un fichier (SHA-256 / SHA-1 / MD5)",
        "en": "Verify file integrity (SHA-256 / SHA-1 / MD5)",
    },
    "toolbox_hash_title_simple": {
        "fr": "Vérifier qu'un fichier téléchargé n'est pas abîmé",
        "en": "Check if a downloaded file is corrupted",
    },
    "toolbox_hash_title_adv": {
        "fr": "Vérificateur d'empreintes numériques (SHA-256 / SHA-1 / MD5)",
        "en": "Checksum Verifier (SHA-256 / SHA-1 / MD5)",
    },
    "toolbox_hash_desc": {
        "fr": "Calcule les empreintes d'un fichier .iso ou .img pour vérifier qu'il n'est pas corrompu.",
        "en": "Compute checksums of an .iso or .img file to verify it is not corrupted.",
    },
    "toolbox_hash_desc_simple": {
        "fr": "Optionnel : compare votre fichier ISO avec le code officiel fourni sur le site.",
        "en": "Optional: compare your ISO file with the official checksum from the download site.",
    },
    "toolbox_hash_desc_adv": {
        "fr": "Calcul en flux continu et comparaison en temps constant.",
        "en": "Streaming computation and constant-time checksum comparison.",
    },
    "toolbox_hash_btn_simple": {
        "fr": "Vérifier le fichier",
        "en": "Verify file",
    },
    "toolbox_hash_btn_adv": {
        "fr": "Calculer et vérifier",
        "en": "Compute & Verify",
    },
    "toolbox_hash_file_ph": {
        "fr": "Fichier à vérifier (.iso, .img)...",
        "en": "File to verify (.iso, .img)...",
    },
    "toolbox_hash_expected_ph": {
        "fr": "Code SHA-256 attendu (facultatif)...",
        "en": "Expected SHA-256 code (optional)...",
    },
    "toolbox_btn_browse": {
        "fr": "Parcourir...",
        "en": "Browse...",
    },
    "toolbox_btn_copy": {
        "fr": "Copier",
        "en": "Copy",
    },
    "toolbox_hash_choose": {
        "fr": "Choisir un fichier...",
        "en": "Choose file...",
    },
    "toolbox_hash_calc_btn": {
        "fr": "Calculer les empreintes",
        "en": "Compute checksums",
    },
    "toolbox_hash_expected": {
        "fr": "Empreinte attendue (facultatif) :",
        "en": "Expected checksum (optional):",
    },
    "toolbox_hash_match": {
        "fr": "Correspondance validée !",
        "en": "Checksum verified match!",
    },
    "toolbox_hash_mismatch": {
        "fr": "Empreinte différente !",
        "en": "Checksum mismatch!",
    },
    "toolbox_speed_title": {
        "fr": "Test de débit séquentiel",
        "en": "Sequential read benchmark",
    },
    "toolbox_speed_desc": {
        "fr": "Mesure la vitesse de lecture continue réelle de la clé USB (Mo/s).",
        "en": "Measure continuous read speed of the USB drive (MB/s).",
    },
    "toolbox_speed_btn": {
        "fr": "Lancer le test de vitesse",
        "en": "Run speed benchmark",
    },
    "toolbox_bench_title_simple": {
        "fr": "Vitesse de la clé USB",
        "en": "USB Drive Speed",
    },
    "toolbox_bench_desc_simple": {
        "fr": "Mesure la rapidité de lecture de votre clé sur ce port USB.",
        "en": "Measures read speed of your USB drive on this port.",
    },
    "toolbox_bench_btn_simple": {
        "fr": "Mesurer la vitesse",
        "en": "Measure speed",
    },
    "toolbox_bench_title_adv": {
        "fr": "Diagnostic de débit séquentiel (256 Mo)",
        "en": "Sequential Read Benchmark (256 MB)",
    },
    "toolbox_bench_desc_adv": {
        "fr": "Évalue la vitesse de lecture brute sur le contrôleur USB.",
        "en": "Evaluates raw read throughput on the USB controller.",
    },
    "toolbox_bench_btn_adv": {
        "fr": "Lancer le benchmark",
        "en": "Run benchmark",
    },
    "toolbox_speed_res": {
        "fr": "Vitesse de lecture moyenne : {speed}",
        "en": "Average read speed: {speed}",
    },
    "toolbox_badblocks_title": {
        "fr": "Contrôle de surface (badblocks)",
        "en": "Surface scan (badblocks)",
    },
    "toolbox_bb_title_simple": {
        "fr": "Vérifier l'état de la clé USB",
        "en": "Check USB Drive Health",
    },
    "toolbox_bb_desc_simple": {
        "fr": "Recherche les erreurs matérielles sans rien effacer.",
        "en": "Searches for hardware errors without erasing anything.",
    },
    "toolbox_bb_btn_simple": {
        "fr": "Tester la fiabilité",
        "en": "Test drive health",
    },
    "toolbox_bb_title_adv": {
        "fr": "Contrôle de surface (secteurs défectueux)",
        "en": "Surface Scan (bad blocks)",
    },
    "toolbox_bb_desc_adv": {
        "fr": "Analyse des blocs physiques et détection de fausses clés.",
        "en": "Physical block inspection and counterfeit drive detection.",
    },
    "toolbox_bb_btn_adv": {
        "fr": "Démarrer l'analyse",
        "en": "Start surface scan",
    },
    "toolbox_bb_destructive_chk": {
        "fr": "Test complet en écriture + lecture (efface tout pour tester la capacité réelle)",
        "en": "Full write + read test (wipes everything to test real hardware capacity)",
    },
    "toolbox_badblocks_desc": {
        "fr": "Recherche d'éventuels secteurs défectueux sur la clé USB.",
        "en": "Search for bad sectors on the USB storage drive.",
    },
    "toolbox_badblocks_ro": {
        "fr": "Mode lecture seule (sécurisé, ne modifie pas les données)",
        "en": "Read-only mode (safe, does not modify data)",
    },
    "toolbox_badblocks_rw": {
        "fr": "Mode écriture destructeur (-w) (ATTENTION: efface le disque)",
        "en": "Destructive write mode (-w) (WARNING: wipes disk)",
    },
    "toolbox_badblocks_btn": {
        "fr": "Démarrer l'analyse de surface",
        "en": "Start surface scan",
    },
    "toolbox_reset_title": {
        "fr": "Remise à zéro d'usine de la clé",
        "en": "Factory Reset & Wipe USB Drive",
    },
    "toolbox_reset_desc": {
        "fr": "Efface les partitions résiduelles (tables MBR/GPT) et reformate la clé proprement.",
        "en": "Wipes residual partitions (MBR/GPT tables) and creates a clean filesystem.",
    },
    "toolbox_wipe_title_simple": {
        "fr": "Réparer une clé USB (tout effacer)",
        "en": "Repair USB Drive (wipe everything)",
    },
    "toolbox_wipe_desc_simple": {
        "fr": "Si votre clé affiche une capacité réduite ou est illisible, ce nettoyage restaure toute sa taille normale.",
        "en": "If your drive shows reduced capacity or errors, this restores its full original size.",
    },
    "toolbox_wipe_btn_simple": {
        "fr": "Tout effacer et réparer",
        "en": "Wipe & Repair Drive",
    },
    "toolbox_wipe_title_adv": {
        "fr": "Remise à zéro d'usine (Clean & Partition)",
        "en": "Factory Reset (Clean & Partition)",
    },
    "toolbox_wipe_desc_adv": {
        "fr": "Supprime toutes les signatures et recrée une table propre.",
        "en": "Erases all partition signatures and creates a clean partition table.",
    },
    "toolbox_wipe_btn_adv": {
        "fr": "Effacer et restaurer d'usine",
        "en": "Factory Reset USB Drive",
    },
    "toolbox_wipe_lbl_table": {
        "fr": "Table :",
        "en": "Table:",
    },
    "toolbox_wipe_lbl_format": {
        "fr": "Format :",
        "en": "Format:",
    },
    "toolbox_wipe_lbl_name": {
        "fr": "Nom :",
        "en": "Label:",
    },
    "toolbox_reset_fs": {
        "fr": "Formater en :",
        "en": "Format as:",
    },
    "toolbox_reset_btn": {
        "fr": "Remettre la clé à zéro",
        "en": "Reset USB drive",
    },
    "toolbox_clone_title": {
        "fr": "Sauvegarde et restauration d'image disque",
        "en": "Disk Image Backup & Restore",
    },
    "toolbox_backup_title": {
        "fr": "Cloner la clé USB vers un fichier image (.img)",
        "en": "Clone USB drive to disk image (.img)",
    },
    "toolbox_backup_desc": {
        "fr": "Crée une copie brute secteur par secteur de la clé USB, avec option de compression gzip.",
        "en": "Creates a raw sector-by-sector copy of the USB drive, with optional gzip compression.",
    },
    "toolbox_backup_btn": {
        "fr": "Sauvegarder l'image (.img)...",
        "en": "Backup disk image (.img)...",
    },
    "toolbox_clone_desc": {
        "fr": "Sauvegardez l'intégralité de votre clé vers un fichier image, ou restaurez une image précédente.",
        "en": "Backup your entire USB drive to an image file, or restore a previous image.",
    },
    "toolbox_clone_backup_opt": {
        "fr": "Créer une image .img / .img.gz de la clé",
        "en": "Backup USB drive to .img / .img.gz",
    },
    "toolbox_clone_restore_opt": {
        "fr": "Restaurer une image .img vers la clé",
        "en": "Restore an .img image to USB drive",
    },
    "toolbox_clone_dest": {
        "fr": "Fichier image :",
        "en": "Image file:",
    },
    "toolbox_clone_btn_backup": {
        "fr": "Sauvegarder",
        "en": "Backup",
    },
    "toolbox_clone_btn_restore": {
        "fr": "Restaurer",
        "en": "Restore",
    },
    "toolbox_qemu_title": {
        "fr": "Tester la clé en machine virtuelle (QEMU)",
        "en": "Test boot without restarting (QEMU)",
    },
    "toolbox_qemu_desc": {
        "fr": "Ouvre une machine virtuelle en lecture seule (-snapshot) pour vérifier le menu de démarrage.",
        "en": "Launches a read-only virtual machine (-snapshot) to test the boot menu.",
    },
    "toolbox_qemu_mode": {
        "fr": "Mode de démarrage :",
        "en": "Boot mode:",
    },
    "toolbox_qemu_ovmf": {
        "fr": "UEFI (OVMF)",
        "en": "UEFI (OVMF)",
    },
    "toolbox_qemu_bios": {
        "fr": "BIOS hérité (Legacy)",
        "en": "Legacy BIOS",
    },
    "toolbox_qemu_btn": {
        "fr": "Lancer le test virtuel QEMU",
        "en": "Launch QEMU virtual test",
    },

    # Catalog
    "catalog_title": {
        "fr": "Télécharger des images officielles vérifiées",
        "en": "Download official verified OS images",
    },
    "catalog_desc": {
        "fr": "Images directes depuis les serveurs officiels des distributions.",
        "en": "Direct images from distribution official servers.",
    },
    "catalog_filter_all": {
        "fr": "Tous",
        "en": "All",
    },
    "catalog_filter_all_adv": {
        "fr": "Toutes",
        "en": "All",
    },
    "catalog_cat_all_simple": {
        "fr": "Tous",
        "en": "All",
    },
    "catalog_cat_current_simple": {
        "fr": "Systèmes courants",
        "en": "Standard Systems",
    },
    "catalog_cat_rescue_simple": {
        "fr": "Dépannage",
        "en": "Rescue & Repair",
    },
    "catalog_cat_security_simple": {
        "fr": "Sécurité",
        "en": "Security & Pentest",
    },
    "catalog_cat_all_adv": {
        "fr": "Toutes",
        "en": "All",
    },
    "catalog_cat_desktop_adv": {
        "fr": "Linux Desktop",
        "en": "Linux Desktop",
    },
    "catalog_cat_security_adv": {
        "fr": "Sécurité & Pentest",
        "en": "Security & Pentest",
    },
    "catalog_cat_rescue_adv": {
        "fr": "Secours & Disque",
        "en": "Rescue & Disk",
    },
    "catalog_folder_lbl": {
        "fr": "Dossier : {path}",
        "en": "Folder: {path}",
    },
    "catalog_folder_btn": {
        "fr": "Dossier...",
        "en": "Folder...",
    },
    "catalog_btn_use_simple": {
        "fr": "Mettre sur une clé",
        "en": "Flash to USB",
    },
    "catalog_btn_use_adv": {
        "fr": "Flash direct",
        "en": "Direct Flash",
    },
    "catalog_btn_ventoy_simple": {
        "fr": "+ Multi-systèmes",
        "en": "+ Multi-OS",
    },
    "catalog_btn_ventoy_adv": {
        "fr": "+ Ventoy",
        "en": "+ Ventoy",
    },
    "catalog_btn_dl_simple": {
        "fr": "Télécharger",
        "en": "Download",
    },
    "catalog_btn_dl_adv": {
        "fr": "Télécharger ISO",
        "en": "Download ISO",
    },
    "catalog_btn_checksum": {
        "fr": "Somme SHA-256",
        "en": "SHA-256 Hash",
    },
    "catalog_dest_folder": {
        "fr": "Dossier de téléchargement :",
        "en": "Download destination folder:",
    },
    "catalog_btn_browse": {
        "fr": "Parcourir...",
        "en": "Browse...",
    },
    "catalog_btn_download": {
        "fr": "Télécharger",
        "en": "Download",
    },
    "catalog_btn_use": {
        "fr": "Utiliser pour créer la clé",
        "en": "Use to flash USB",
    },
    "catalog_status_downloading": {
        "fr": "Téléchargement en cours : {pct}% ({speed})",
        "en": "Downloading: {pct}% ({speed})",
    },
    "catalog_status_done": {
        "fr": "Téléchargement terminé !",
        "en": "Download complete!",
    },
    "catalog_status_error": {
        "fr": "Erreur de téléchargement : {err}",
        "en": "Download error: {err}",
    },

    # Dialogs & Global chrome
    "menu_theme": {
        "fr": "Changer thème Clair / Sombre",
        "en": "Toggle Light / Dark Theme",
    },
    "menu_preflight": {
        "fr": "Outils système installés...",
        "en": "Installed System Tools...",
    },
    "menu_logs": {
        "fr": "Afficher / masquer le journal",
        "en": "Show / Hide Log Console",
    },
    "menu_about": {
        "fr": "À propos de BootForge",
        "en": "About BootForge",
    },
    "status_ready": {
        "fr": "Prêt",
        "en": "Ready",
    },
    "status_waiting": {
        "fr": "En attente d’instructions",
        "en": "Waiting for instructions",
    },
    "status_badge_ready": {
        "fr": "PRÊT",
        "en": "READY",
    },
    "status_badge_working": {
        "fr": "EN COURS",
        "en": "WORKING",
    },
    "status_badge_done": {
        "fr": "TERMINÉ",
        "en": "DONE",
    },
    "status_badge_error": {
        "fr": "ERREUR",
        "en": "ERROR",
    },
    "bar_journal_title": {
        "fr": "Journal des opérations",
        "en": "Operation Log",
    },
    "bar_journal_copy": {
        "fr": "Copier",
        "en": "Copy",
    },
    "bar_journal_clear": {
        "fr": "Effacer",
        "en": "Clear",
    },
    "bar_journal_btn": {
        "fr": "Journal ({count})",
        "en": "Logs ({count})",
    },
    "bar_btn_cancel": {
        "fr": "Annuler",
        "en": "Cancel",
    },

    "dialog_tweaks_title": {
        "fr": "Paramètres Windows 11 (autounattend.xml)",
        "en": "Windows 11 Setup Tweaks (autounattend.xml)",
    },
    "dialog_tweaks_grp_bypass": {
        "fr": "Contournements Windows 11 (24H2 / 25H2)",
        "en": "Windows 11 Bypasses (24H2 / 25H2)",
    },
    "dialog_tweaks_chk_enabled": {
        "fr": "Activer l'injection autounattend.xml sur la clé",
        "en": "Enable autounattend.xml injection on USB drive",
    },
    "dialog_tweaks_chk_hw": {
        "fr": "Ignorer TPM 2.0, Secure Boot, RAM et CPU (BypassTPMCheck)",
        "en": "Bypass TPM 2.0, Secure Boot, RAM & CPU requirements",
    },
    "dialog_tweaks_chk_nro": {
        "fr": "Autoriser l'installation sans compte Microsoft (BypassNRO)",
        "en": "Allow installation without Microsoft Account (BypassNRO)",
    },
    "dialog_tweaks_chk_bitlocker": {
        "fr": "Désactiver le chiffrement automatique BitLocker",
        "en": "Disable automatic BitLocker device encryption",
    },
    "dialog_tweaks_chk_telemetry": {
        "fr": "Désactiver les questions de télémétrie et suivi",
        "en": "Disable telemetry and diagnostic questions",
    },
    "dialog_tweaks_chk_bloat": {
        "fr": "Désactiver les applications suggérées et widgets promotionnels",
        "en": "Disable suggested apps and promotional widgets",
    },
    "dialog_tweaks_chk_key": {
        "fr": "Passer l'écran de saisie de clé produit (activation ultérieure)",
        "en": "Skip product key prompt (activate later)",
    },
    "dialog_tweaks_grp_acct": {
        "fr": "Compte local et langue",
        "en": "Local Account & Locale",
    },
    "dialog_tweaks_chk_local": {
        "fr": "Créer un compte administrateur local automatiquement",
        "en": "Automatically create local administrator account",
    },
    "dialog_tweaks_lbl_user": {
        "fr": "Nom d'utilisateur :",
        "en": "Username:",
    },
    "dialog_tweaks_lbl_pass": {
        "fr": "Mot de passe (optionnel) :",
        "en": "Password (optional):",
    },
    "dialog_tweaks_lbl_loc": {
        "fr": "Langue et clavier :",
        "en": "Language & Keyboard:",
    },
    "dialog_tweaks_tab_opts": {
        "fr": "Options",
        "en": "Options",
    },
    "dialog_tweaks_tab_xml": {
        "fr": "Aperçu XML",
        "en": "XML Preview",
    },
    "dialog_tweaks_lbl_xml_preview": {
        "fr": "Aperçu généré pour autounattend.xml :",
        "en": "Generated autounattend.xml preview:",
    },
    "dialog_tweaks_btn_copy_xml": {
        "fr": "Copier le code XML",
        "en": "Copy XML code",
    },
    "dialog_tweaks_btn_cancel": {
        "fr": "Annuler",
        "en": "Cancel",
    },
    "dialog_tweaks_btn_save": {
        "fr": "Enregistrer les options",
        "en": "Save Options",
    },

    "dialog_confirm_title_simple": {
        "fr": "Confirmer l'effacement",
        "en": "Confirm Erase",
    },
    "dialog_confirm_title_adv": {
        "fr": "Confirmation requise",
        "en": "Confirmation Required",
    },
    "dialog_confirm_intro_simple": {
        "fr": "Tout le contenu de cette clé USB va être effacé.\n{detail}",
        "en": "All content on this USB drive will be erased.\n{detail}",
    },
    "dialog_confirm_intro_adv": {
        "fr": "Toutes les partitions existantes sur ce disque vont être supprimées.\n{detail}",
        "en": "All existing partitions on this disk will be deleted.\n{detail}",
    },
    "dialog_confirm_action_lbl": {
        "fr": "Action : <b>{action}</b>",
        "en": "Action: <b>{action}</b>",
    },
    "dialog_confirm_target_lbl": {
        "fr": "Périphérique cible : <b>{dev}</b>",
        "en": "Target device: <b>{dev}</b>",
    },
    "dialog_confirm_prompt_msg": {
        "fr": "Pour confirmer l'opération, tapez <b>{word}</b> ci-dessous :",
        "en": "To confirm this operation, type <b>{word}</b> below:",
    },
    "dialog_confirm_word": {
        "fr": "EFFACER",
        "en": "ERASE",
    },
    "dialog_confirm_btn_exec": {
        "fr": "Effacer et démarrer",
        "en": "Erase and start",
    },
    "dialog_confirm_btn_cancel": {
        "fr": "Annuler",
        "en": "Cancel",
    },

    "dialog_preflight_win_title": {
        "fr": "Outils système et composants",
        "en": "System Tools & Components",
    },
    "dialog_preflight_win_core": {
        "fr": "Outils Windows natifs indispensables",
        "en": "Core required native Windows tools",
    },
    "dialog_preflight_win_embedded": {
        "fr": "Composants BootForge embarqués",
        "en": "Embedded BootForge components",
    },
    "dialog_preflight_win_uefi": {
        "fr": "Chargeur UEFI:NTFS (démarrage Windows NTFS)",
        "en": "UEFI:NTFS bootloader (Windows NTFS boot)",
    },
    "dialog_preflight_win_opt": {
        "fr": "Outils facultatifs (recommandés)",
        "en": "Optional tools (recommended)",
    },
    "dialog_preflight_btn_refresh": {
        "fr": "Actualiser",
        "en": "Refresh",
    },
    "dialog_preflight_btn_close": {
        "fr": "Fermer",
        "en": "Close",
    },
    "dialog_preflight_status_ok": {
        "fr": "✅ Présent",
        "en": "✅ Installed",
    },
    "dialog_preflight_status_miss": {
        "fr": "❌ Manquant",
        "en": "❌ Missing",
    },
    "dialog_preflight_status_ready": {
        "fr": "✅ Prêt",
        "en": "✅ Ready",
    },
    "dialog_preflight_status_dl": {
        "fr": "⚡ Téléchargement auto",
        "en": "⚡ Auto-download",
    },
    "dialog_preflight_status_opt": {
        "fr": "⚪ Optionnel",
        "en": "⚪ Optional",
    },

    "dialog_about_title": {
        "fr": "À propos de BootForge",
        "en": "About BootForge",
    },
    "dialog_about_edition_win": {
        "fr": "Version {ver} · Windows Edition (64 bits)",
        "en": "Version {ver} · Windows Edition (64-bit)",
    },
    "dialog_about_desc": {
        "fr": "Studio de création de clés USB bootables (Rufus direct) et multi-systèmes (Ventoy).\nConçu et développé par NiceMank.",
        "en": "Studio for bootable (Rufus direct) and multi-boot (Ventoy) USB drives.\nDesigned and developed by NiceMank.",
    },
    "dialog_about_link_repo": {
        "fr": "Dépôt GitHub",
        "en": "GitHub Repository",
    },
    "dialog_about_link_issues": {
        "fr": "Signaler un bug",
        "en": "Report an Issue",
    },
    "dialog_about_link_license": {
        "fr": "Licence GPL-3.0",
        "en": "GPL-3.0 License",
    },
    "dialog_about_btn_close": {
        "fr": "Fermer",
        "en": "Close",
    },

    "dialog_json_title": {
        "fr": "Aperçu /ventoy/ventoy.json",
        "en": "Preview /ventoy/ventoy.json",
    },
    "dialog_json_intro": {
        "fr": "Configuration générée automatiquement pour la clé Ventoy :",
        "en": "Automatically generated configuration for Ventoy drive:",
    },
    "dialog_json_btn_copy": {
        "fr": "Copier",
        "en": "Copy",
    },
    "dialog_json_btn_close": {
        "fr": "Fermer",
        "en": "Close",
    },

    # Dialogs
    "dialog_tweaks_title": {
        "fr": "Ajustements d'installation Windows 11",
        "en": "Windows 11 Setup Tweaks",
    },
    "dialog_tweaks_tab_opts": {
        "fr": "Options de personnalisation",
        "en": "Customization options",
    },
    "dialog_tweaks_tab_xml": {
        "fr": "Aperçu autounattend.xml",
        "en": "autounattend.xml Preview",
    },
    "dialog_tweaks_tpm": {
        "fr": "Contourner les exigences TPM 2.0, Secure Boot et 4 Go de RAM",
        "en": "Bypass TPM 2.0, Secure Boot & 4 GB RAM requirements",
    },
    "dialog_tweaks_account": {
        "fr": "Créer un compte local hors ligne (sans compte Microsoft)",
        "en": "Create a local offline account (bypass Microsoft Account)",
    },
    "dialog_tweaks_user": {
        "fr": "Nom d'utilisateur local :",
        "en": "Local username:",
    },
    "dialog_tweaks_pass": {
        "fr": "Mot de passe (optionnel) :",
        "en": "Password (optional):",
    },
    "dialog_tweaks_bitlocker": {
        "fr": "Désactiver le chiffrement automatique de lecteur BitLocker",
        "en": "Disable automatic BitLocker device encryption",
    },
    "dialog_tweaks_telemetry": {
        "fr": "Désactiver la télémétrie et le suivi de diagnostic",
        "en": "Disable diagnostic data collection & telemetry",
    },
    "dialog_tweaks_bloat": {
        "fr": "Désactiver les applications suggérées par défaut",
        "en": "Disable consumer bloatware & suggested apps",
    },
    "dialog_tweaks_drivers": {
        "fr": "Injecter des pilotes supplémentaires ($WinPEDriver$)",
        "en": "Inject additional drivers ($WinPEDriver$)",
    },
    "dialog_tweaks_driver_folder": {
        "fr": "Dossier de pilotes :",
        "en": "Driver folder:",
    },
    "dialog_tweaks_save": {
        "fr": "Enregistrer les réglages",
        "en": "Save settings",
    },
    "dialog_tweaks_cancel": {
        "fr": "Annuler",
        "en": "Cancel",
    },
    "dialog_confirm_title": {
        "fr": "Confirmation de destruction des données",
        "en": "Confirm Data Destruction",
    },
    "dialog_confirm_warn": {
        "fr": "Toutes les données présentes sur ce périphérique seront définitivement effacées !",
        "en": "All data on this storage device will be permanently erased!",
    },
    "dialog_confirm_prompt": {
        "fr": "Pour confirmer, tapez le mot « {word} » ci-dessous :",
        "en": "To confirm, type the word '{word}' below:",
    },
    "dialog_confirm_word": {
        "fr": "EFFACER",
        "en": "ERASE",
    },
    "dialog_confirm_btn": {
        "fr": "Effacer et continuer",
        "en": "Erase and continue",
    },
    "dialog_confirm_cancel": {
        "fr": "Annuler",
        "en": "Cancel",
    },
    "dialog_preflight_title": {
        "fr": "Outils système installés",
        "en": "Installed System Tools",
    },
    "dialog_preflight_core": {
        "fr": "Outils indispensables",
        "en": "Core required tools",
    },
    "dialog_preflight_opt": {
        "fr": "Outils optionnels",
        "en": "Optional tools",
    },
    "dialog_preflight_close": {
        "fr": "Fermer",
        "en": "Close",
    },
    "dialog_about_title": {
        "fr": "À propos de BootForge",
        "en": "About BootForge",
    },
    "dialog_about_sub": {
        "fr": "Studio de création de clés USB bootables et multi-boot",
        "en": "Bootable & multi-boot USB creation studio",
    },
    "dialog_about_author": {
        "fr": "Auteur",
        "en": "Author",
    },
    "dialog_about_license": {
        "fr": "Licence",
        "en": "License",
    },
    "dialog_about_version": {
        "fr": "Version",
        "en": "Version",
    },
    # Auto-updater
    "menu_updates": {
        "fr": "Mises à jour...",
        "en": "Check for Updates...",
    },
    "app_updates_tip": {
        "fr": "Vérifier les mises à jour sécurisées sur GitHub",
        "en": "Check for verified GitHub updates",
    },
    "update_title": {
        "fr": "Mises à jour de BootForge",
        "en": "BootForge Software Updates",
    },
    "update_current_ver": {
        "fr": "Version installée : v{ver}",
        "en": "Installed version: v{ver}",
    },
    "update_auto_chk": {
        "fr": "Vérifier automatiquement au démarrage",
        "en": "Check automatically at startup",
    },
    "update_btn_check": {
        "fr": "Vérifier maintenant",
        "en": "Check now",
    },
    "update_btn_install": {
        "fr": "Télécharger et installer",
        "en": "Download & Install",
    },
    "update_btn_skip": {
        "fr": "Ignorer cette version",
        "en": "Skip this version",
    },
    "update_btn_open_release": {
        "fr": "Voir sur GitHub",
        "en": "Open GitHub Release",
    },
    "update_btn_close": {
        "fr": "Fermer",
        "en": "Close",
    },
    "update_status_idle": {
        "fr": "Prêt à interroger GitHub Releases (vérification Ed25519 + SHA-256).",
        "en": "Ready to query GitHub Releases (Ed25519 + SHA-256 verification).",
    },
    "update_status_checking": {
        "fr": "Vérification en cours via HTTPS (Ed25519)...",
        "en": "Checking GitHub Releases over verified HTTPS...",
    },
    "update_status_up_to_date": {
        "fr": "✅ BootForge est à jour (v{ver}).",
        "en": "✅ BootForge is up to date (v{ver}).",
    },
    "update_status_available": {
        "fr": "🆕 Mise à jour vérifiée disponible : v{ver} ({asset})",
        "en": "🆕 Verified update available: v{ver} ({asset})",
    },
    "update_status_blocked": {
        "fr": "🛡️ Installation automatique bloquée : {reason}",
        "en": "🛡️ Automatic update blocked: {reason}",
    },
    "update_status_busy": {
        "fr": "⏳ Opération USB active : installation différée jusqu'à la fin du travail.",
        "en": "⏳ USB operation active: installation deferred until job finishes.",
    },
    "update_status_downloading": {
        "fr": "Téléchargement sécurisé et contrôle SHA-256...",
        "en": "Downloading and verifying SHA-256...",
    },
    "update_status_installed_deb": {
        "fr": "✅ Paquet .deb installé. Redémarrez BootForge pour utiliser la nouvelle version.",
        "en": "✅ .deb package installed. Restart BootForge to launch the new version.",
    },
    "update_status_extracted": {
        "fr": "✅ Archive vérifiée extraite dans : {path}",
        "en": "✅ Verified archive extracted to: {path}",
    },
    "update_status_win_launched": {
        "fr": "🚀 Installateur Windows vérifié lancé. Fermeture de BootForge...",
        "en": "🚀 Verified Windows installer launched. Closing BootForge...",
    },
}


def t(key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
    """Translate a key with optional string interpolation."""
    target_lang = lang or _current_language
    entry = STRINGS.get(key)
    if not entry:
        return key

    val = entry.get(target_lang) or entry.get(DEFAULT_LANGUAGE) or entry.get("en") or key
    if kwargs:
        try:
            return val.format(**kwargs)
        except Exception:
            return val
    return val


def format_bytes_i18n(num_bytes: int | float, lang: Optional[str] = None) -> str:
    """Format bytes nicely with localized unit suffix."""
    target_lang = lang or _current_language
    n = max(float(num_bytes or 0), 0.0)
    units = ["o", "Ko", "Mo", "Go", "To"] if target_lang == "fr" else ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while n >= 1024.0 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(n)} {units[idx]}"
    return f"{n:.2f} {units[idx]}"


def format_speed_i18n(bytes_per_sec: Optional[float], lang: Optional[str] = None) -> str:
    """Format speed with localized unit suffix."""
    if bytes_per_sec is None or bytes_per_sec <= 0:
        return ""
    target_lang = lang or _current_language
    unit = "Mo/s" if target_lang == "fr" else "MB/s"
    return f"{bytes_per_sec / (1024.0 * 1024.0):.1f} {unit}"


# Initialize language on import
init_language()
