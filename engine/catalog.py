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
        "category_en": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "simple_category_en": "Standard Systems",
        "description": "Distribution LTS stable avec prise en charge de la persistance casper-rw.",
        "description_en": "Stable LTS distribution with casper-rw persistence support.",
        "simple_description": "Système Linux populaire, simple à utiliser au quotidien.",
        "simple_description_en": "Popular, beginner-friendly Linux operating system for everyday use.",
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
        "category_en": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "simple_category_en": "Standard Systems",
        "description": "Environnement classique léger, rapide et compatible avec la majorité des PC.",
        "description_en": "Lightweight, fast desktop environment compatible with most PC hardware.",
        "simple_description": "Interface familière proche de Windows, idéale pour débuter sous Linux.",
        "simple_description_en": "Familiar Windows-like interface, ideal for Linux newcomers.",
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
        "category_en": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "simple_category_en": "Standard Systems",
        "description": "Base Debian stable pour dépannage, bureautique ou environnement Live.",
        "description_en": "Rock-solid Debian stable system for office work, rescue, or live use.",
        "simple_description": "Système léger et très fiable pour travailler ou dépanner un ordinateur.",
        "simple_description_en": "Lightweight and rock-solid system to work or troubleshoot PCs.",
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
        "category_en": "Rescue & Recovery",
        "simple_category": "Dépannage",
        "simple_category_en": "Troubleshooting",
        "description": "Boîte à outils d’urgence : GParted, Clonezilla, testdisk, chroot, réparations EFI.",
        "description_en": "Emergency rescue toolkit: GParted, Clonezilla, testdisk, chroot, EFI repairs.",
        "simple_description": "Outil de secours pour récupérer des fichiers ou réparer un PC en panne.",
        "simple_description_en": "Rescue toolkit to recover files or repair an unbootable computer.",
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
        "category_en": "Rescue & Recovery",
        "simple_category": "Dépannage",
        "simple_category_en": "Troubleshooting",
        "description": "Gestionnaire de partitions dédié pour redimensionner et réparer vos disques.",
        "description_en": "Dedicated disk partition manager to resize, check, and repair storage drives.",
        "simple_description": "Utilitaire simple pour organiser, réduire ou effacer des disques durs.",
        "simple_description_en": "Simple utility to organize, resize or format hard disks and partitions.",
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
        "category_en": "Security & Pentest",
        "simple_category": "Sécurité",
        "simple_category_en": "Security",
        "description": "Système Live audit réseau et sécurité avec persistance Debian (/ union).",
        "description_en": "Live OS for network and security auditing with Debian union persistence.",
        "simple_description": "Système spécialisé pour les audits réseau et l’analyse de sécurité.",
        "simple_description_en": "Specialized operating system for network audits and security testing.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~4.4 Go",
        "url": "https://cdimage.kali.org/current/kali-linux-2026.1-live-amd64.iso",
        "checksum_page": "https://cdimage.kali.org/current/SHA256SUMS",
        "persistence_hint": "debian-persistence",
    },
]


def get_localized_catalog(lang: str = "fr") -> List[Dict[str, str]]:
    """Return catalog items with localized descriptions and category names."""
    is_en = lang == "en"
    result: List[Dict[str, str]] = []
    for item in OFFICIAL_CATALOG:
        entry = dict(item)
        if is_en:
            entry["category"] = item.get("category_en", item["category"])
            entry["simple_category"] = item.get("simple_category_en", item["simple_category"])
            entry["description"] = item.get("description_en", item["description"])
            entry["simple_description"] = item.get("simple_description_en", item["simple_description"])
        result.append(entry)
    return result


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
