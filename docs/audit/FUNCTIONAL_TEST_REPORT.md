# Rapport d'Audit Fonctionnel — BootForge v1.0.1

Date : 2026-11-13  
Méthode : Inspection ligne par ligne du code, exécution CLI/moteur, simulations `--dry-run`, tests unitaires (`pytest`), instanciation graphique offscreen GTK4 (`xvfb-run`) et PyQt6 (`QT_QPA_PLATFORM=offscreen`).

---

## 1. Matrice de Vérification des Fonctionnalités

### A. Flash Direct (`bootforge_engine.py`, `win_backend.py`, `iso_inspector.py`)
- **Inspection ISO9660 pure Python + `7z l -slt`** : Fonctionnelle (testée sur `efi.img` réel et ISO synthétique). Écart mineur : `7z` appelé sans `--` avant le chemin de l'image ; chemin WIM codé en dur sur `sources/install.wim` (manque `x64/sources/install.wim` des ISO multi-arch Microsoft).
- **GPT / MBR / FAT32 / NTFS / exFAT / UEFI:NTFS** : Logique complète et planification validée en `--dry-run` sur `/dev/sdb`.
- **Découpe WIM en `.swm` (`wimlib-imagex`)** : Implémentée dans le moteur, non exécutable localement car `wimtools` est absent sur l'hôte (testable via mocks).
- **Mode brut DD (`.img`, `.gz`, `.xz`)** : Implémenté ; défaut constaté : la barre de progression compare les octets décompressés écrits à la taille du fichier compressé d'entrée (`percent` atteint 100 % prématurément).
- **Persistance Linux (`casper-rw` / `persistence`)** : Planifiée et implémentée sous Linux ; sous Windows, `bootforge_win/views_direct.py:627` force `"persistence_type": "casper-rw"` sans option dans l'UI.

### B. Multi-Boot Ventoy (`ventoy_manager.py`, `views_ventoy.py`)
- **Détection partition `VTOYEFI` + état existant + édition `ventoy.json`** : Implémentée et couverte par tests unitaires.
- **Téléchargement & installation/mise à jour (`Ventoy2Disk.sh` / `Ventoy2Disk.exe`)** : Implémentée, avec vulnérabilité critique de bypass SHA-256 en cas d'erreur de récupération et extraction `tar` non filtrée (corrigée en Phase 5).

### C. Windows 11 & `autounattend.xml` (`unattend_builder.py`)
- **Génération XML, échappement des caractères spéciaux, contournements TPM/SB/RAM/NRO/BitLocker/Télémetrie/Bloatware, compte local, `$WinPEDriver$`** : Validée structurellement par `xml.etree.ElementTree`. Écart mineur : `driver_dir` copié avec suivi par défaut des symlinks ; validation Windows réelle non testable sous Linux (`BLOCKED` hors runner Windows).

### D. Boîte à Outils USB (`bootforge_engine.py`, `win_backend.py`, `views_toolbox.py`)
- **Sommes de contrôle (SHA-256 / SHA-1 / MD5)** : Fonctionnelle et validée sur fichier réel USB (`/run/media/niceman/KALI LINUX/md5sum.txt`).
- **Remise à zéro d'usine (`usb_wipe_reset`), Benchmark (`usb_benchmark`), Badblocks (`usb_badblocks`), Sauvegarde (`usb_backup_img`)** : Implémentées côté Linux (`--dry-run` validé) ; côté Windows, `usb_badblocks` est défectueux en gestion d'`OSError` et ne réalise pas de test d'écriture réel en mode destructif.
- **Restauration d'image (`usb_restore_img`)** : **MANQUANTE** dans le moteur et les deux interfaces (seules les traductions i18n existent dans `engine/i18n.py`).
- **Simulation QEMU/OVMF** : Implémentée dans les UI, non exécutable localement (`qemu-system-x86_64` absent) ; lance QEMU en `root` via `pkexec` sous Linux sans politique Polkit dédiée.

### E. Catalogue Officiel & Interfaces (GTK4, PyQt6, i18n, Mode Simple/Avancé)
- **Catalogue (`engine/catalog.py`)** : Contient 6 distributions vérifiées. Contradiction avec `README.md`/`README.fr.md` qui annoncent Windows 11, Fedora, Arch, Proxmox et Clonezilla.
- **GTK4 (`bootforge_ui/window.py`) & PyQt6 (`bootforge_win/window.py`)** : Instanciées avec succès en offscreen (`xvfb-run` et `QT_QPA_PLATFORM=offscreen`), bascule Simple/Avancé et FR/EN opérationnelle sans crash. Défaut : de nombreuses chaînes restent codées en dur en français lors du changement dynamique de langue (`bootforge_ui/window.py`, `views_toolbox.py`, `views_direct.py`, `bootforge_win/window.py`).

## 2. Environnement Windows

- Le code Windows (`engine/win_backend.py` + `bootforge_win/*`) est compilé (`py_compile`), testé via mocks sous `pytest`, et l'interface PyQt6 démarre sous `QT_QPA_PLATFORM=offscreen`.
- **Limite explicite** : L'exécution physique sur noyau Windows (`diskpart`, `ShellExecuteExW runas`, `CreateFileW PhysicalDriveX`, PyInstaller, NSIS) ne peut pas être validée sur cet hôte Linux et reste marquée **NON TESTÉE SUR MATÉRIEL WINDOWS** (vérification dédiée via tests mockés + CI `windows-latest`).
