# Audit de Sécurité Approfondi — BootForge v1.0.1

Date : 2026-11-13  
Périmètre : Moteur (`engine/`), Interfaces (`bootforge_ui/`, `bootforge_win/`), Packaging (`packaging/`, `data/`, `.github/workflows/`).

---

## Synthèse des vulnérabilités confirmées

| ID | Sévérité | Composant | Résumé | Référence / CWE |
| :--- | :--- | :--- | :--- | :--- |
| SEC-01 | **Critique** | `engine/disk_utils.py` | `parse_lsblk_json` n'inspecte qu'un seul niveau d'enfants (`children`), manquant les conteneurs LUKS/LVM/dm-crypt imbriqués hébergeant `/`. | CWE-284 / CWE-706 |
| SEC-02 | **Haute** | `engine/disk_utils.py` | `validate_target_device` ne vérifie ni `is_system_disk` directement, ni les points de montage sous-jacents, ni si l'ISO source ou le fichier de sortie réside sur le disque cible. | CWE-20 / CWE-362 |
| SEC-03 | **Critique** | `engine/ventoy_manager.py` | En cas d'échec de récupération du SHA-256 distant (`except Exception: expected_sha256 = None`), BootForge télécharge et exécute `Ventoy2Disk.sh` en tant que `root` sans vérification d'intégrité. | CWE-353 / CWE-494 |
| SEC-04 | **Haute** | `engine/ventoy_manager.py`, `bootforge_engine.py` | Vérification de traversée de chemin via `str(path).startswith(str(base))` vulnérable au contournement par préfixe frère (`/tmp/cache_evil` vs `/tmp/cache`), et `tar.extractall()` sans filtrage `data` des symlinks/hardlinks. | CWE-22 / CWE-59 |
| SEC-05 | **Haute** | `engine/bootforge_engine.py` | `ensure_uefi_ntfs_image()` télécharge `uefi-ntfs.img` sans empreinte SHA-256 attendue ni renommage atomique, puis l'écrit par `dd` en root sur la clé. | CWE-494 |
| SEC-06 | **Haute** | `bootforge_ui/backend.py`, `bootforge_win/backend.py` | Le téléchargeur du catalogue autorise explicitement les redirections `https -> http`, ne vérifie pas les domaines contre une liste blanche et ne contrôle pas les sommes de contrôle post-téléchargement. | CWE-319 / CWE-353 |
| SEC-07 | **Moyenne** | `engine/bootforge_engine.py` | En cas d'échec de `umount` dans `finally`, `shutil.rmtree(tmp_mnt)` s'exécute quand même en root sur un point de montage possiblement actif ; `shutil.copytree` sur `driver_dir` suit les symlinks par défaut. | CWE-59 / CWE-459 |
| SEC-08 | **Moyenne** | `engine/win_backend.py` | Interpolation de chaînes dans des scripts PowerShell (`Mount-DiskImage`, `Get-WmiObject`) n'échappant que `'`; valeurs de retour de `FSCTL_LOCK_VOLUME`/`FSCTL_DISMOUNT_VOLUME` ignorées ; boucle infinie potentielle sur `OSError` dans `usb_badblocks`. | CWE-78 / CWE-252 |
| SEC-09 | **Moyenne** | `bootforge_ui/backend.py`, `bootforge_win/backend.py` | `pkexec qemu-system-x86_64` exécute l'émulateur QEMU complet en `root` sans politique Polkit dédiée ; `cancel()` termine `pkexec` sans propager l'arrêt propre au groupe de processus enfant. | CWE-250 / CWE-404 |
| SEC-10 | **Faible** | `requirements-win.txt`, `.github/workflows/ci-release.yml` | Dépendances Windows non épinglées (`PyQt6>=6.6.0`, `pyinstaller>=6.0.0`) et installation CI sans `requirements-win.txt` ni hachages. `7z l -slt` appelé sans séparateur `--`. | CWE-829 / CWE-88 |

---

## Détails techniques et scénarios de reproduction

### SEC-01 — Contournement de détection système sur disques chiffrés (LUKS/LVM)
- **Localisation** : `engine/disk_utils.py:182-194`.
- **Preuve** : `parse_lsblk_json` itère uniquement sur `for child in dev.get("children") or []`. Dans une topologie standard Debian/Kali chiffrée (`nvme0n1p3` -> `crypt` (`sda3_crypt`) -> `lvm` (`vg-root`) -> `/`), le point de montage `/` se trouve au 2e ou 3e niveau d'enfants. `system_reasons` reste vide si `tran=usb` ou si le disque n'est pas filtré autrement.
- **Correction visée** : Parcours récursif de tout l'arbre `children` pour agréger l'ensemble des `mountpoints`, `fstype` et drapeaux système sur tous les niveaux.

### SEC-02 — Lacunes de `validate_target_device` & collisions source/destination
- **Localisation** : `engine/disk_utils.py:279-330`, `engine/bootforge_engine.py:422-745`.
- **Preuve** :
  1. `validate_target_device` ne vérifie pas directement `if match.is_system_disk: raise SafetyViolationError(...)` ; elle ré-évalue partiellement `CRITICAL_MOUNTPOINTS` en égalité stricte (`mp in CRITICAL_MOUNTPOINTS or mp.startswith("/boot")`) et ignore le montage éventuel du disque parent ou des sous-répertoires (`/var/lib`, `/usr/local`, `/etc/fstab`).
  2. Rien n'empêche l'utilisateur de flasher une ISO stockée sur `/run/media/user/USB/image.iso` vers `/dev/sdb`, ni de sauvegarder `usb_backup_img` vers une partition de `/dev/sdb` en train d'être démontée/écrite.
- **Correction visée** : Rejeter systématiquement `match.is_system_disk`, résoudre `os.path.realpath(device_path)` avant correspondance, vérifier que `image_path` / `driver_dir` / `output_path` ne résident sur aucun point de montage des partitions du périphérique cible.

### SEC-03 & SEC-04 — Téléchargement Ventoy non vérifié et extraction dangereuse
- **Localisation** : `engine/ventoy_manager.py:106-143, 204-216`, `engine/bootforge_engine.py:585-588`.
- **Preuve** :
  1. Lignes 117-118 : `except Exception: expected_sha256 = None` suivi de `if expected_sha256 and ...` : toute erreur ou altération sur la lecture du fichier de hachage permet de contourner silencieusement la vérification SHA-256 avant d'exécuter `Ventoy2Disk.sh` en `root`.
  2. Lignes 141 et 212 : `str(member_path).startswith(str(cache.resolve()))` valide à tort `/tmp/cache-malicious/file` pour une base `/tmp/cache`.
  3. Ligne 214 : `tf.extractall(cache)` n'utilise ni `filter="data"` ni rejet explicite des `issym()`/`islnk()`/`ischr()`/`isblk()`.
- **Correction visée** : Exiger une empreinte SHA-256 valide et échouer (fail-closed) en son absence ; remplacer tous les `startswith` sur chemins par `Path.is_relative_to()` ; rejeter tout membre lien symbolique/hardlink/périphérique et utiliser `filter="data"` sous Python 3.12+.

### SEC-05 & SEC-06 — Téléchargements UEFI:NTFS et Catalogue OS
- **Localisation** : `engine/bootforge_engine.py:141-166`, `bootforge_ui/backend.py:285-305`, `bootforge_win/backend.py:315-332`.
- **Preuve** :
  1. `ensure_uefi_ntfs_image()` télécharge `uefi-ntfs.img` sans hachage de référence et écrit directement dans `assets/uefi-ntfs/uefi-ntfs.img` (fichier tronqué utilisable en cas de coupure réseau).
  2. `run_catalog_download()` autorise explicitement `http` après redirection (`if final_scheme not in ("https", "http"):`) et ne valide aucun domaine autorisé ni hachage final.
- **Correction visée** : Épingler/vérifier l'empreinte SHA-256 connue de `uefi-ntfs.img` (ainsi que des fichiers `.part` renommés atomiquement), imposer HTTPS strict sur toutes les redirections, valider les hôtes autorisés et vérifier automatiquement la somme de contrôle quand un manifeste/empreinte officielle est disponible.

### SEC-07, SEC-08, SEC-09 — Montages, PowerShell, Locks Win32 et Privilèges QEMU
- **Localisation** : `engine/bootforge_engine.py:350-480, 628-640`, `engine/win_backend.py:516-520, 962-985`, `bootforge_ui/backend.py:355-380`.
- **Preuve** :
  - `win_backend.py:516` ignore le booléen retourné par `DeviceIoControl(FSCTL_LOCK_VOLUME)`.
  - `win_backend.py:974-980` en cas d'`OSError` sur `fin.read(chunk)` n'avance pas la position du curseur via `fin.seek(done + chunk)`, provoquant une boucle sur le même secteur défectueux ; de plus `usb_badblocks` avec `destructive=True` se limite à `diskpart clean` + lecture au lieu d'un motif écriture/relecture ou d'un rejet explicite non simulé.
  - `bootforge_ui/backend.py:371` lance `pkexec qemu-system-x86_64` sans profil Polkit dédié et expose QEMU en `root`.
- **Correction visée** : Vérifier le code retour `umount` avant `rmtree` et désactiver `symlinks=False` sur `copytree` ; sécuriser l'invocation PowerShell via arguments encodés/variables d'environnement ; corriger la progression/seek Win32 ; renforcer les contrôles d'erreur et signaler explicitement les privilèges QEMU ou n'utiliser `pkexec` que lorsque `/dev/sdX` l'exige.
