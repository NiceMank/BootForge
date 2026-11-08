"""Shared OS download catalog and Windows tweak defaults for BootForge."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

OFFICIAL_CATALOG: List[Dict[str, str]] = [
    {
        "id": "ubuntu-2404",
        "name": "Ubuntu Desktop 24.04.2 LTS",
        "version": "24.04.2 LTS",
        "category": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "description": "Distribution LTS stable avec prise en charge de la persistance casper-rw.",
        "simple_description": "Système Linux populaire, simple à utiliser au quotidien.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~5.8 Go",
        "url": "https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-desktop-amd64.iso",
        "checksum_page": "https://releases.ubuntu.com/24.04.2/SHA256SUMS",
        "persistence_hint": "casper-rw",
    },
    {
        "id": "mint-cinnamon",
        "name": "Linux Mint 22.1 Cinnamon",
        "version": "22.1 Xia",
        "category": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "description": "Environnement classique léger, rapide et compatible avec la majorité des PC.",
        "simple_description": "Interface familière proche de Windows, idéale pour débuter sous Linux.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~2.9 Go",
        "url": "https://mirrors.kernel.org/linuxmint/stable/22.1/linuxmint-22.1-cinnamon-64bit.iso",
        "checksum_page": "https://mirrors.kernel.org/linuxmint/stable/22.1/sha256sum.txt",
        "persistence_hint": "casper-rw",
    },
    {
        "id": "debian-live",
        "name": "Debian 12 Live GNOME",
        "version": "12.9 Bookworm",
        "category": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "description": "Base Debian stable pour dépannage, bureautique ou environnement Live.",
        "simple_description": "Système léger et très fiable pour travailler ou dépanner un ordinateur.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~3.3 Go",
        "url": "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/debian-live-12.9.0-amd64-gnome.iso",
        "checksum_page": "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/SHA256SUMS",
        "persistence_hint": "debian-persistence",
    },
    {
        "id": "systemrescue",
        "name": "SystemRescue",
        "version": "11.03",
        "category": "Secours & Disque",
        "simple_category": "Dépannage",
        "description": "Boîte à outils d’urgence : GParted, Clonezilla, testdisk, chroot, réparations EFI.",
        "simple_description": "Outil de secours pour récupérer des fichiers ou réparer un PC en panne.",
        "architecture": "x86_64",
        "size_hint": "~980 Mo",
        "url": "https://fastly-cdn.system-rescue.org/releases/11.03/systemrescue-11.03-amd64.iso",
        "checksum_page": "https://www.system-rescue.org/Download/",
        "persistence_hint": "none",
    },
    {
        "id": "gparted-live",
        "name": "GParted Live",
        "version": "1.7.0-1",
        "category": "Secours & Disque",
        "simple_category": "Dépannage",
        "description": "Gestionnaire de partitions dédié pour redimensionner et réparer vos disques.",
        "simple_description": "Utilitaire simple pour organiser, réduire ou effacer des disques durs.",
        "architecture": "x86_64",
        "size_hint": "~560 Mo",
        "url": "https://downloads.sourceforge.net/gparted/gparted-live-1.7.0-1-amd64.iso",
        "checksum_page": "https://gparted.org/download.php",
        "persistence_hint": "none",
    },
    {
        "id": "kali-live",
        "name": "Kali Linux Live",
        "version": "2026.x Rolling",
        "category": "Sécurité & Pentest",
        "simple_category": "Sécurité",
        "description": "Système Live audit réseau et sécurité avec persistance Debian (/ union).",
        "simple_description": "Système spécialisé pour les audits réseau et l’analyse de sécurité.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~4.4 Go",
        "url": "https://cdimage.kali.org/current/kali-linux-2026.1-live-amd64.iso",
        "checksum_page": "https://cdimage.kali.org/current/SHA256SUMS",
        "persistence_hint": "debian-persistence",
    },
]


def default_downloads_dir() -> Path:
    for candidate in (Path.home() / "Téléchargements", Path.home() / "Downloads", Path.home()):
        if candidate.is_dir():
            return candidate
    return Path.home()


def default_win_tweaks() -> Dict[str, Any]:
    return {
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
