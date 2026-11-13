# BootForge — Rapport de Validation Phase 2 & Préparation Release

**Date d'exécution** : 2026-11-13  
**Version contrôlée** : `1.0.1`  
**Dépôt local** : `/run/media/niceman/Nice/ProjetsWeb/BootForge` (`origin`: `https://github.com/NiceMank/BootForge`)  
**Scope** : Audit indépendant des correctifs Phase 1, provisionnement Ed25519 Auto-Update, extension des tests négatifs/régression, contrôles matériels non destructifs sur `/dev/sdb` (`KALI LINUX`), audit de compatibilité Windows & préparation des 4 artefacts de release (sans publication ni modification de secrets GitHub).

---

## 1. Synthèse de l'audit indépendant (Défauts Phase 2 identifiés & corrigés)

Un réexamen complet du code modifié en Phase 1 (`engine/disk_utils.py`, `engine/net_security.py`, `engine/ventoy_manager.py`, `engine/bootforge_engine.py`, `engine/win_backend.py`, `engine/updater.py`, `packaging/sign_release.py`, `packaging/build_windows.py`, `packaging/win_installer.nsi`, `.github/workflows/ci-release.yml`) a mis en évidence **9 défauts ou faiblesses supplémentaires**, tous corrigés :

| # | Fichier / Symbole | Défaut identifié | Correction appliquée |
|---|-------------------|------------------|----------------------|
| 1 | `engine/updater.py` (`decode_signature_bytes`) | `sig_raw.strip()` exécuté *avant* le contrôle `len == 64` d'une signature binaire brute : une signature Ed25519 brute commençant ou finissant par `0x09`/`0x0a`/`0x0d`/`0x20` était tronquée à < 64 octets et rejetée à tort. | Vérification de la signature brute 64 octets sur `sig_raw` avant tout `.strip()`. |
| 2 | `engine/updater.py` (`ed25519_verify`) | Seul le point identité `(0, 1, 1, 0)` était rejeté ; les points d'ordre faible (8-torsion) n'étaient pas testés. | Rejet systématique de tout point tel que `[8]A == O` via trois doublements successifs (`_ed_add`). |
| 3 | `engine/updater.py` (`check_for_update`) | `chosen["url"]` n'était pas lié au préfixe officiel `https://github.com/NiceMank/BootForge/releases/download/<tag>/` lors de la validation du manifeste (seule la liste d'hôtes `GITHUB_ALLOWED_HOSTS` était vérifiée au téléchargement). | Validation stricte `validate_https_url(..., allowed_hosts=GITHUB_ALLOWED_HOSTS)` et contrôle exact du préfixe dépôt + tag. |
| 4 | `engine/updater.py` (`apply_downloaded_update`, `linux_deb`) | `pkexec apt-get install -y ~/.cache/bootforge/updates/<pkg>.deb` échoue sur Debian/Ubuntu car l'utilisateur non privilégié `_apt` ne peut pas traverser un `$HOME`/`.cache` en `0700` (`Permission denied`). | Passage de `-o APT::Sandbox::User=root` à `pkexec apt-get ... install -y <deb>` (fallback `pkexec dpkg -i`). |
| 5 | `engine/updater.py` (`apply_downloaded_update`, `windows_setup`) | Les drapeaux `/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` (spécifiques à Inno Setup) étaient passés à un installeur NSIS (`packaging/win_installer.nsi`), pour lequel l'option silencieuse est `/S`. | Remplacement par `"/S"` dans le `runner` et `ShellExecuteW`. |
| 6 | `packaging/sign_release.py` (`cmd_keygen`, `cmd_build_manifest`, `main`) | `--keygen` imprimait `PRIVATE_KEY_B64=...` sur `stdout` (risque de fuite dans les journaux/terminaux) et `cmd_build_manifest` retournait `0` même sans clé privée. | Ajout de `--private-out` (écriture `0o600` strictement hors dépôt avec refus via `_is_inside_repo`), blocage de l'affichage `stdout` par défaut (sauf `--unsafe-stdout`), et ajout de `--require-signature` activé dans `.github/workflows/ci-release.yml` pour `publish-release`. |
| 7 | `engine/disk_utils.py` (`validate_target_device`) | `os.path.realpath(device_path) if os.path.islink(...) else device_path` ne normalisait pas un chemin non-symlink comme `/dev/foo/../../dev/nvme0n1`. | Canonicalisation via `os.path.realpath` si `os.path.lexists(device_path)`, sinon `os.path.normpath`. |
| 8 | `engine/bootforge_engine.py` (`execute_flash_direct`) | Le nettoyage de `tmp_pmnt` (`linux-persistence`) utilisait encore `umount` + `shutil.rmtree(..., ignore_errors=True)` au lieu de `_safe_umount_cleanup(tmp_pmnt)`. | Remplacement par `_safe_umount_cleanup(tmp_pmnt)` (ne supprime le répertoire que si `umount` retourne `0`). |
| 9 | `engine/win_backend.py` (`_write_raw_windows_disk`, `execute_win_flash_direct`, `usb_restore_img`, `usb_badblocks`) & `packaging/build_windows.py` | (a) `_write_raw_windows_disk` n'appliquait pas de borne `max_bytes=dev_info.size_bytes` et mesurait la progression `.img.gz`/`.img.xz` par rapport à la taille compressée ; (b) `usb_badblocks` ne troncait pas `total` à un multiple de 512 octets ; (c) `build_windows.py` ne passait pas `--hidden-import=engine.bootforge_engine` / `engine.win_backend`. | Ajout de `max_bytes` + `raw_reader` (`_open_windows_image_stream`), alignement `(size // 512) * 512` sur `usb_badblocks`, et ajout des deux `--hidden-import` dans `packaging/build_windows.py`. |

> Aucune régression fonctionnelle n'a été constatée sur les tests existants après application de ces correctifs.

---

## 2. État de l'Auto-Update Ed25519 & Provisionnement

### Vérifications effectuées
- **Primitif cryptographique (`engine/updater.py`)** : Vérification pure-Python RFC 8032 (`ed25519_verify`) validée en parité bit à bit contre `cryptography.hazmat.primitives.asymmetric.ed25519`, avec rejet de l'identité, des points d'ordre 2 / 8-torsion, `s >= L`, signatures tronquées/altérées, et prise en charge sûre des signatures brutes 64 octets contenant des octets blancs.
- **Chaîne manifeste & anti-downgrade** : `verify_manifest_bytes` vérifie `schema_version == 1`, `product == "BootForge"`, `repo == "NiceMank/BootForge"`, `key_id`, et la signature Ed25519 ; `check_for_update` valide `SemVer` (anti-downgrade strict `manifest_ver > cur_ver` et concordance `tag == tag_name`), la correspondance exacte du préfixe d'URL `https://github.com/NiceMank/BootForge/releases/download/<tag>/`, puis la présence et le format SHA-256.
- **Téléchargement & application** : `download_verified_update` diffère pendant une opération USB active (`is_busy_fn`) et télécharge via `download_https_atomic` (TLS >= 1.2, anti-downgrade HTTP, vérification SHA-256 avant `os.replace`) ; `apply_downloaded_update` ré-effectue le SHA-256 anti-TOCTOU avant d'installer.
- **Séparation test vs production & provisionnement** :
  - Clé publique de production provisionnée dans `engine/updater.py` (`PRODUCTION_KEY_ID = "bootforge-release-2026"`, `PUBLIC_KEY_HEX = "3f452d9cc024643b69eee6d72ddda3ec9d62c6ea9148a9f8564bb398e79c7469"`, `is_release_key_provisioned() == True`).
  - L'absence de clé provisionnée, de `BOOTFORGE_MANIFEST.json`, de `.sig`, ou une signature issue d'une clé de test / invalide provoque un refus fail-closed (`unprovisioned_key`, `missing_manifest`, `invalid_signature`) sans proposition d'installation automatique.
  - La clé privée correspondante est stockée uniquement hors dépôt (`/home/niceman/.config/bootforge-secrets/release-2026.key`, mode `0600`) et n'a jamais été lue, affichée ni commise ; aucune release ni secret GitHub n'a été modifié.

### Action opérateur restante (GitHub Actions)
- Importer le contenu base64 de `/home/niceman/.config/bootforge-secrets/release-2026.key` dans le secret GitHub Actions **`BOOTFORGE_SIGNING_KEY`** avant le premier tag `v*` (sans jamais commiter le fichier de clé privée).

---

## 3. Tests unitaires, négatifs et couverture

### Nouveaux tests ajoutés
Création de `engine/tests/test_phase2_regression.py` (7 suites de tests négatifs / régression) couvrant :
1. **`disk_utils.py`** : traversée `/dev/foo/../../dev/nvme0n1`, rejet partitions (`/dev/sdb1`, `/dev/nvme0n1p1`), rejet périphérique lecture seule, périphérique disparu de `lsblk`, JSON `lsblk` corrompu et `TimeoutExpired`.
2. **`net_security.py`** : interruption socket mid-stream avec préservation du fichier existant et nettoyage `.part`, annulation utilisateur (`cancel_check`), dépassement `max_bytes`, redirection vers domaine non autorisé.
3. **`updater.py` & `sign_release.py`** : signature brute 64 octets avec `0x20`/`0x0a`, point d'ordre 2/8-torsion rejeté, spoofing d'URL inter-dépôts bloqué dans un manifeste signé, `APT::Sandbox::User=root` pour `.deb`, drapeau NSIS `/S` pour `.exe`, `is_release_key_provisioned() == True` + rejet d'une signature issue d'une clé de test, garde `--keygen` (refus sans `--private-out`, refus écriture dans le dépôt, fichier `0o600` hors dépôt) et refus `--require-signature` sans clé.
4. **`ventoy_manager.py`** : archives `.tar.gz`/`.zip` corrompues, rejet des liens durs (`LNKTYPE`), absence d'asset SHA-256 (fail-closed) et `PermissionError` sur le répertoire de cache.
5. **`bootforge_engine.py`** : dépassement de capacité décompressée `.img.gz` avant écriture, écriture `.img.xz` valide, `_safe_umount_cleanup` préservant le contenu monté si `umount` échoue, job sur fichier absent.
6. **`win_backend.py` (simulé)** : échec & timeout `_run_diskpart`, erreur & JSON invalide `_run_powershell_json`, fermeture de tous les handles volumes lors d'un échec `_lock_and_dismount_disk_volumes`, dépassement capacité `.img.gz` dans `usb_restore_img`, détection de secteurs corrompus `0xAA55` en `usb_badblocks` destructif simulé.

### Résultats d'exécution & couverture mesurée (`trace.Trace`)
- `python3 -m compileall -q bootforge_ui bootforge_win engine packaging` : **succès (`exit 0`)**.
- `pytest -v` (après provisionnement de la clé publique) : **46/46 réussis en 4.38s** (0 échec, 0 ignoré) répartis sur `test_engine.py` (8), `test_i18n.py` (7), `test_phase2_regression.py` (7), `test_security_hardening.py` (10), `test_ui_helpers.py` (4), `test_updater.py` (5), `test_win_backend.py` (5).
- **Couverture par lignes exécutables (`trace.Trace` sur `engine/*.py`, mesurée sur la suite à 46 tests, `RC=0`)** :

| Module | Lignes exécutées / totales | Couverture |
|--------|---------------------------|------------|
| `unattend_builder.py` | 75 / 76 | **98.7%** |
| `net_security.py` | 97 / 111 | **87.4%** |
| `updater.py` | 290 / 367 | **79.0%** |
| `disk_utils.py` | 217 / 296 | **73.3%** |
| `i18n.py` | 72 / 110 | **65.5%** |
| `ventoy_manager.py` | 136 / 249 | **54.6%** |
| `win_backend.py` | 276 / 617 | **44.7%** *(chemins Win32 natifs simulés via mocks)* |
| `catalog.py` | 7 / 23 | **30.4%** |
| `iso_inspector.py` | 76 / 263 | **28.9%** |
| `bootforge_engine.py` | 110 / 678 | **16.2%** *(opérations Linux root `parted`/`mkfs`/`dd`/`mount` non exécutables sans root/destruction)* |

---

## 4. Contrôles matériels non destructifs sur `/dev/sdb` (`KALI LINUX`)

Tous les contrôles ci-dessous ont été menés **en lecture seule** en conservant `/dev/sdb1` monté sur `/run/media/niceman/KALI LINUX` sans aucune écriture, démontage ou altération :

- **Détection & attributs matériels (`lsblk`, `udevadm`, `/sys/block/sdb`, `engine.bootforge_engine devices`)** :
  - Disque cible : `/dev/sdb` — `VendorCo ProductCode`, série `6838251221392670722`, `VID:PID 346d:5678`, USB `480 Mbps`, table `gpt`, taille `15 728 640 000` octets (`30 720 000` secteurs de 512 o), `/sys/block/sdb/removable=1`, `/sys/block/sdb/ro=0`, `is_system_disk=False`.
  - Partition unique : `/dev/sdb1`, `vfat` (`FAT32`), label `KALI LINUX`, UUID `9E02-153C`, `15 727 566 848` octets, montée `rw` sur `/run/media/niceman/KALI LINUX` (`13 807 362 048` octets disponibles, 12% utilisés).
  - Disques internes système détectés et protégés : `/dev/nvme0n1` (`512 Go`, montages `/`, `/boot/efi`, `/home`, `[SWAP]`) et `/dev/sda` (`250 Go SATA`).
- **Lecture, intégrité & sommes de contrôle** :
  - Vérification d'un échantillon de **100 fichiers présents** listés dans `/run/media/niceman/KALI LINUX/md5sum.txt` : **100 contrôlés, 0 divergence (`mismatches=0`)**. *(Remarque : l'ensemble complet de `md5sum.txt` contient quelques entrées `.disk/*` et `README.*` absentes de l'extraction FAT32 `KALI LINUX`, déjà signalées au démarrage de `md5sum -c`)*.
  - Hachage via `bootforge_engine.py hash-file` sur le fichier le plus volumineux de la clé (`/run/media/niceman/KALI LINUX/pool/main/f/fonts-noto-cjk/fonts-noto-cjk-extra_20240730+repack1-1_all.deb`, `134 125 676` octets) :
    - `SHA-256`: `16081e6982d2c8de4d773cf124c09ea25e6e1e472cd7fc62a58a52a1c241b41a`
    - `SHA-1`: `afb49b8b4e3340783d527d11f7bd26601b7cb91b`
    - `MD5`: `52cfc56715f262db416499a0b35c2912`
  - Contrôle à froid de lecture séquentielle (`POSIX_FADV_DONTNEED` avant lecture sur le même fichier de `134.1 Mo`) : **`20.34 MiB/s`** (`6.288s`), cohérent avec un lien USB 2.0 `480 Mbps`.
- **État système fichiers & observation comportement CLI `ventoy-state`** :
  - `fsck.vfat -n /dev/sdb1` en utilisateur standard retourne `code 6` (`open: Permission denied`, le nœud `/dev/sdb1` étant `root:disk` `0660`), ce qui confirme que la vérification bloc bas niveau requiert les privilèges `root` (`pkexec`).
  - `python3 -m engine.bootforge_engine ventoy-state "/run/media/niceman/KALI LINUX"` retourne correctement le bilan JSON (`15 710 789 632` octets totaux, `13 807 362 048` libres, `ventoy_json: {}`, 4 images EFI/img détectées). *(Note mineure : passer `/dev/sdb` au lieu du point de montage à `ventoy-state` retourne proprement `ENGINE_ERROR: Point de montage introuvable : /dev/sdb`).*
- **Protections destructives & retrait** :
  - Blocage effectif par `validate_target_device` et CLI `run-job --dry-run` (`exit_code 2`, `SAFETY_BLOCKED`) sur `/dev/nvme0n1`, `/dev/sda`, `/dev/sdb1`, `/dev/foo/../../dev/nvme0n1`, et périphérique inexistant `/dev/sdz` (simulant un retrait USB).
  - Blocage effectif de `validate_paths_not_on_device` sur `/run/media/niceman/KALI LINUX/boot/grub/efi.img` ciblant `/dev/sdb`.
  - Plans `--dry-run` validés sans écriture (`exit_code 0`) pour `flash_direct`, `ventoy_install`, `usb_backup_img` et `usb_restore_img` sur `/dev/sdb`.
  - Re-vérification post-tests : `/dev/sdb1` toujours monté avec `13 807 362 048` octets libres et empreinte SHA-256 de `fonts-noto-cjk-extra_20240730+repack1-1_all.deb` inchangée (`16081e6982...1b41a`).

---

## 5. Compatibilité Windows (Simulé vs Réel)

| Composant | Statut de validation | Nature de la preuve |
|-----------|----------------------|---------------------|
| **PyQt6 UI** (`bootforge_win/window.py`) | Validé en rendu offscreen Linux | `QT_QPA_PLATFORM=offscreen BOOTFORGE_NO_STARTUP_UPDATE=1` : instanciation, `show()`, `processEvents()`, `close()` réussis sur Linux (`PYQT6_P2_SMOKE_OK`). **Non exécuté sur un OS Windows réel localement.** |
| **Backend disque, PowerShell, `diskpart`, `WriteFile`/ioctl** (`engine/win_backend.py`) | Validé uniquement par tests unitaires simulés (mocks) | `test_win_backend.py` + `test_phase2_regression.py` valident l'inventaire WMI/PS, les scripts `diskpart`, la protection système, le verrouillage/fermeture de handles, le blocage `max_bytes`, la vérification de motifs `0xAA55`, et l'isolation `$env:BF_ISO_PATH`. **Non exécuté contre un véritable noyau Windows (`PhysicalDriveN` / `diskpart.exe` / `dism.exe`).** |
| **Updater Windows** (`windows_setup`, `windows_portable`) | Validé en simulation Linux | Décompression ZIP portable réelle testée ; `windows_setup` validé avec mock `runner` vérifiant le drapeau NSIS `/S` et revérification SHA-256 anti-TOCTOU. L'appel `ShellExecuteW(..., "runas", ...)` n'est pas exécutable sous Linux. |
| **PyInstaller & NSIS** (`packaging/build_windows.py`, `packaging/win_installer.nsi`) | Audités et vérifiés statiquement | Tous les fichiers référencés (`assets/icon.ico`, `LICENSE`, `README.md`, `packaging/win_main.py`, `requirements-win.txt`) existent ; `__version__ = "1.0.1"` cohérent dans `bootforge_ui` et `bootforge_win`; `--hidden-import=engine.bootforge_engine` et `engine.win_backend` ajoutés ; `makensis` (`v3.12-1`) et `pyinstaller` identifiés ; workflow `.github/workflows/ci-release.yml` (`build-windows` sur `windows-latest`) valide YAML. **La compilation `.exe` réelle dépend du runner CI `windows-latest` sur GitHub Actions.** |

---

## 6. Préparation des 4 artefacts & CI/CD

### Vérification réelle sur Linux
1. **`bootforge_1.0.1_all.deb`** (reconstruit via `bash packaging/build_deb.sh` : `1 475 116` octets, `Installed-Size: 2744`) :
   - `dpkg-deb --info` & `dpkg-deb -c` : **83 entrées**, aucune trace `__pycache__`/`.pyc`, présence de la règle PolicyKit, dépendances système (`python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `parted`, `gdisk`, `dosfstools`, `ntfs-3g`, `exfatprogs`, `e2fsprogs`, `7zip | p7zip-full`, `util-linux`, `udisks2`).
   - `desktop-file-validate data/io.github.nicemank.BootForge.desktop` : **exit `0`**.
   - Smoke test GTK4/Libadwaita sous `xvfb-run` : **`GTK4_P2_SMOKE_RC: 0`**.
2. **`BootForge-1.0.1-linux.tar.gz`** (`1 517 454` octets, **46 entrées**) :
   - Décompressé en dossier temporaire isolé (`tarfile(..., filter="data")`) : présence du lanceur `bootforge`, exécution réussie de `python3 -m engine.bootforge_engine deps` (`6` dépendances vérifiées).
   *(Remarque workspace : `release/` local contient encore deux anciens artefacts `1.0.0` non suivis par Git de la session initiale ; en CI, le dossier `release/` part d'un environnement vierge).*

### Vérification simulée des 4 artefacts & du pipeline de signature CI/CD
- Validation YAML de `.github/workflows/ci-release.yml` : 3 jobs détectés (`build-linux`, `build-windows`, `publish-release`).
- Simulation complète du pipeline `sign_release.py --require-signature` dans un dossier temporaire contenant les 2 artefacts Linux réels renommés en `1.2.0` et 2 artefacts Windows synthétiques (`BootForge-1.2.0-win64-setup.exe`, `BootForge-1.2.0-win64-portable.zip`) avec une paire Ed25519 éphémère en mémoire :
  - Génération réussie de `BOOTFORGE_MANIFEST.json` (4 canaux : `linux_deb`, `linux_tar`, `windows_portable`, `windows_setup`) et `BOOTFORGE_MANIFEST.json.sig`.
  - Vérification positive via `updater.check_for_update` sur chacun des **4 canaux** avec la clé de test, et rejet `invalid_signature` par la clé publique de production réelle (`bootforge-release-2026`) ainsi que rejet `unprovisioned_key` lorsque `trusted_keys` est vide.

---

## 7. Risques résiduels

1. **Secret GitHub `BOOTFORGE_SIGNING_KEY` à renseigner avant publication** : la clé publique est intégrée dans `engine/updater.py`, mais tant que `BOOTFORGE_SIGNING_KEY` n'est pas configuré dans les secrets du dépôt GitHub, le job `publish-release` bloquera sur `--require-signature`.
2. **Exécution Windows réelle non testée localement** : `bootforge_win`, `win_backend.py` (`diskpart`, `dism`, WMI, `DeviceIoControl`/`WriteFile`, `ShellExecuteW`), `build_windows.py` et `win_installer.nsi` doivent impérativement être validés par l'exécution réelle du job `build-windows` sur `windows-latest` (et un essai sur machine Windows avec clé physique).
3. **Opérations Linux destructives avec `pkexec`** : l'écriture réelle (`parted`, `mkfs.*`, `dd`, `wimlib-imagex`, `Ventoy2Disk.sh`, `fsck.vfat -n`) n'a pas été exécutée sur `/dev/sdb` afin de préserver intégralement la clé `KALI LINUX` ; ces chemins restent couverts par `--dry-run`, tests unitaires et contrôle statique.
4. **Anciens fichiers `1.0.0` dans `release/` local** : penser à nettoyer `release/*1.0.0*` localement si ce répertoire est utilisé manuellement pour des tests de packaging.
