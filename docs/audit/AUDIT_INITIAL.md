# Audit Initial — BootForge v1.0.1

Date d'audit : 2026-11-13  
Dépôt : `/run/media/niceman/Nice/ProjetsWeb/BootForge` (`https://github.com/NiceMank/BootForge`)

## 1. État Git & Inventaire du dépôt

- **Branche active** : `main`, alignée sur `origin/main` (`df2eef6`, tag `v1.0.1`).
- **Arbre de travail** : propre au démarrage de l'audit.
- **Historique récent** : 6 commits (`2ec1dcc` à `df2eef6`).
- **Volume de code source** : ~11 017 lignes répertoriées sur les composants Python/packaging/doc principaux :
  - `engine/` : `bootforge_engine.py` (899 l.), `win_backend.py` (1018 l.), `disk_utils.py` (547 l.), `ventoy_manager.py` (399 l.), `iso_inspector.py` (379 l.), `unattend_builder.py` (192 l.), `catalog.py` (156 l.), `i18n.py` (1346 l.).
  - `bootforge_ui/` (GTK4/Libadwaita Linux) : 8 fichiers Python (~4 127 l.).
  - `bootforge_win/` (PyQt6 Windows) : 8 fichiers Python (~3 316 l.).
  - `packaging/` & `.github/workflows/ci-release.yml` & `data/` (`bootforge`, `.desktop`, `.policy`).
  - `engine/tests/` : 4 fichiers de tests (`test_engine.py`, `test_i18n.py`, `test_ui_helpers.py`, `test_win_backend.py`).

## 2. Environnement & Dépendances locales (Hôte Linux)

- **Python** : 3.14.8, `pytest` 9.1.1, `PyGObject` (`Gtk 4.0`, `Adw 1`), `PyQt6`, `cryptography`, `PyNaCl` (`nacl`).
- **Outils d'audit absents localement** : `coverage`, `bandit`, `pip-audit`, `semgrep`, `ruff`.
- **Outils système présents (`engine/bootforge_engine.py deps`)** : `lsblk`, `parted`, `sgdisk`, `wipefs`, `mkfs.vfat`, `mkfs.ntfs`, `mkfs.exfat`, `mkfs.ext4`, `7z`, `dd`, `pkexec`, `badblocks`, `smartctl`, `gpg`, `gh`, `dpkg-deb`.
- **Outils optionnels absents localement** : `wimlib-imagex`, `qemu-system-x86_64` (+ OVMF), `mcopy`, `xorriso`.
- **Actifs cachés** : `assets/uefi-ntfs/uefi-ntfs.img` présent localement (`sha256: 72683fa1250eeea772d3399277b434d4e55ba8dd0dc926e52d817e701fc2eb9e`).

## 3. Baseline de Tests Initiale

- `pytest -v` (sans `PYTHONPATH=.`) : **ÉCHEC de collecte** (4 erreurs `ModuleNotFoundError: No module named 'engine'`).
- `python3 -m pytest -v` : **24 passed** en 1.39s (baseline reproductible).
- `python3 -m unittest discover -s engine/tests -v` : **24 passed**.

## 4. État réel des fonctionnalités vs Documentation

| Fonctionnalité annoncée | État réel vérifié dans le code | Écarts / Remarques critiques |
| :--- | :--- | :--- |
| Flash direct ISO / GPT / MBR / FAT32 / NTFS / exFAT / DD | Implémenté (`bootforge_engine.py`, `win_backend.py`) | Progrès DD biaisé pour `.gz`/`.xz` (`written` vs taille compressée). Découpe WIM limitée au chemin exact `sources/install.wim`. |
| UEFI:NTFS | Implémenté | Téléchargement `uefi-ntfs.img` sans vérification SHA-256 ni écriture atomique. |
| Persistance Linux (`casper-rw` / `persistence`) | Implémenté sous Linux | `bootforge_win/views_direct.py` force `"persistence_type": "casper-rw"` sans sélecteur. Risque `rmtree(tmp_mnt)` si `umount` échoue. |
| Multi-boot Ventoy + `ventoy.json` + persistance `.dat` | Implémenté (`ventoy_manager.py`) | Vérification SHA-256 du binaire Ventoy désactivée silencieusement en cas d'erreur réseau. Extraction `tar` sans filtrage strict des liens symboliques (`filter='data'`). |
| Contournements Windows 11 & `autounattend.xml` + `$WinPEDriver$` | Implémenté (`unattend_builder.py`) | `shutil.copytree` sur `driver_dir` suit par défaut les symlinks sans contrôle. |
| Boîte à outils (SHA-256/SHA-1/MD5, benchmark, badblocks, wipe, backup `.img`/`.img.gz`, QEMU) | Partiellement complet | **Restauration d'image (`usb_restore_img`) absente** du moteur et des interfaces malgré les chaînes i18n (`toolbox_clone_restore_opt`, `toolbox_clone_btn_restore`) et le README. `usb_badblocks` Windows n'effectue pas de réel test écriture destructif et n'avance pas le pointeur lors d'`OSError`. |
| Catalogue officiel d'OS (`engine/catalog.py`) | Partiel (6 entrées) | `README.md`/`README.fr.md` annoncent **Windows 11, Fedora, Arch, Proxmox, Clonezilla**, absents de `OFFICIAL_CATALOG` (qui contient Ubuntu, Mint, Debian, SystemRescue, GParted, Kali). Téléchargements sans check d'hôte autorisé, sans blocage de downgrade HTTP, ni vérification SHA-256 automatique. |
| Bilinguisme FR/EN & Modes Simple/Avancé | Implémenté | Plusieurs libellés restent codés en dur en français dans `bootforge_ui/window.py` et `bootforge_win/window.py`. Badge CI du README pointe vers `build.yml` au lieu de `ci-release.yml`. |
| Auto-mise à jour GitHub | Absente | Non implémentée dans v1.0.1. |
