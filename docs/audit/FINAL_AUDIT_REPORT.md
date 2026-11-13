# BootForge — Rapport Final d'Audit, Durcissement, Tests et Auto-Update

**Date :** 2026-11-13  
**Version :** 1.0.1 (`https://github.com/NiceMank/BootForge`)  
**Workspace :** `/run/media/niceman/Nice/ProjetsWeb/BootForge`

---

## 1. Documentation d'audit produite (`docs/audit/`)

- [AUDIT_INITIAL.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/AUDIT_INITIAL.md) — État initial du dépôt, périmètre fonctionnel et dépendances.
- [SECURITY_AUDIT.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/SECURITY_AUDIT.md) — Audit de sécurité complet (périphériques, privilèges, ISO, réseau, configuration, dépendances).
- [USB_HARDWARE_TEST_REPORT.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/USB_HARDWARE_TEST_REPORT.md) — Identification matérielle et validation non destructive sur la clé USB réelle `/dev/sdb`.
- [FUNCTIONAL_TEST_REPORT.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/FUNCTIONAL_TEST_REPORT.md) — Matrice de tests fonctionnels multi-OS.
- [COVERAGE_REPORT.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/COVERAGE_REPORT.md) — Mesure AST `trace` de la couverture des modules `engine/*.py`.
- [UPDATE_SYSTEM_DESIGN.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/UPDATE_SYSTEM_DESIGN.md) — Architecture et modèle cryptographique du système de mise à jour automatique.
- [UPDATE_TEST_REPORT.md](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/docs/audit/UPDATE_TEST_REPORT.md) — Résultats des tests unitaires et hors écran de l'auto-updater.

---

## 2. Corrections & durcissements majeurs appliqués

### A. Protection des disques et opérations destructives
- [engine/disk_utils.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/disk_utils.py) :
  - `is_critical_mountpoint()` étendue aux sous-arborescences système (`/boot/*`, `/var/*`, `/usr/*`, `/etc/*`, `/opt/*`, `/srv/*`, `/snap/*`).
  - `_collect_nested_children()` parcourt récursivement les partitions, conteneurs LUKS/crypt, LVM et RAID dans `parse_lsblk_json()`.
  - `validate_target_device()` résout les liens symboliques (`/dev/disk/by-*`) via `os.path.realpath()` et bloque les partitions, les disques en lecture seule et tout `is_system_disk`.
  - `validate_paths_not_on_device()` interdit qu'une image ISO source ou qu'un fichier de sauvegarde/restauration réside sur une partition montée du périphérique cible.

### B. Transport réseau HTTPS & intégrité d'archives
- [engine/net_security.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/net_security.py) :
  - `build_https_opener()` impose TLS >= 1.2 et rejette via `_StrictHttpsRedirectHandler` toute redirection rétrogradant en HTTP ou sortant de l'allowlist lorsqu'elle est activée.
  - `download_https_atomic()` écrit dans un fichier temporaire `.part`, exécute `flush` + `os.fsync`, vérifie l'empreinte SHA-256 attendue, puis remplace atomiquement via `os.replace`.
- [engine/ventoy_manager.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/ventoy_manager.py) :
  - `_fetch_required_ventoy_sha256()` bloque désormais en **fail-closed** si l'empreinte SHA-256 officielle Ventoy est absente.
  - `safe_extract_zip()` et `safe_extract_tar()` utilisent `Path.is_relative_to()` (suppression du piège de préfixe frère), rejettent liens symboliques, hardlinks et nœuds spéciaux (`filter="data"` sur Python >= 3.12).
  - Nettoyage `umount` + `os.rmdir` corrigé dans `create_persistence_dat_image()`.

### C. Injection, WIM, Restore IMG & Backend Windows
- [engine/iso_inspector.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/iso_inspector.py) : séparateur `--` avant `real_path` dans `7z l -slt --`.
- [engine/bootforge_engine.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/bootforge_engine.py) : condensat SHA-256 épinglé (`UEFI_NTFS_SHA256`), support `x64/sources/install.wim`, copie sans symlinks (`_copy_dir_no_symlinks`), écriture DD compressée avec contrôle de taille cible (`_write_raw_stream_to_device`), ajout de l'action `usb_restore_img`.
- [engine/win_backend.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/win_backend.py) : passage du chemin ISO via variable d'environnement `BF_ISO_PATH` dans PowerShell (supprime tout risque d'injection par guillemets), vérification stricte des retours `DeviceIoControl` lors du verrouillage/démontage des volumes, correction du curseur `usb_badblocks` sur `OSError` + passe d'écriture réelle en mode destructif, ajout de `usb_restore_img`.
- Interfaces [bootforge_ui/](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_ui/) & [bootforge_win/](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_win/) :
  - Câblage de la restauration `.img` / `.img.gz` / `.img.xz` (`usb_restore_img`) derrière `ConfirmDestroyDialog`.
  - Catalogue ISO migré vers `download_https_atomic(..., allowed_hosts=CATALOG_INITIAL_HOSTS)`.
  - Persistance atomique fusionnée (`save_prefs` / `save_win_prefs`) ne détruisant plus la clé `language`.
  - Respect dynamique du type de persistance sous Windows (`bootforge_win/views_direct.py`) et attente propre des `QThread` dans `closeEvent`.

---

## 3. Système de mise à jour automatique sécurisé

- [engine/updater.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/updater.py) :
  - Vérificateur **Ed25519 (RFC 8032)** en Python pur sans aucune dépendance externe au runtime.
  - Interrogation stricte de `NiceMank/BootForge` (`GITHUB_ALLOWED_HOSTS`), vérification du manifeste signé `BOOTFORGE_MANIFEST.json` + `.sig`, contrôle anti-downgrade **SemVer**, sélection automatique par canal (`linux_deb`, `linux_tar`, `windows_setup`, `windows_portable`), contrôle anti-TOCTOU SHA-256 avant exécution, et blocage automatique tant qu'une opération USB est active.
- [packaging/sign_release.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/packaging/sign_release.py) : utilitaire CLI `--keygen` et signature de release pour CI/CD.
- Intégration UI complète FR/EN dans [bootforge_ui/widgets.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_ui/widgets.py) (`UpdatesDialog`) et [bootforge_win/dialogs.py](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_win/dialogs.py) (`UpdatesWinDialog`), avec vérification non bloquante au démarrage désactivable et option *Ignorer cette version*.
- **Important (Action mainteneur) :** `TRUSTED_RELEASE_PUBLIC_KEYS["bootforge-release-2026"]` est volontairement vide (`""`) par sécurité jusqu'à ce que Farel exécute `python3 packaging/sign_release.py --keygen`, dépose la clé publique dans `engine/updater.py` et enregistre la clé privée dans le secret GitHub Actions `BOOTFORGE_SIGNING_KEY`. Tant que la clé n'est pas provisionnée, le client refuse en *fail-closed* l'installation automatique.

---

## 4. Preuves de validation exécutées

| Vérification | Commande / Portée | Résultat observé |
| :--- | :--- | :--- |
| Suite complète `pytest` | `pytest -v` (`pyproject.toml` + 5 fichiers de test) | **39 passed in 4.09s** (0 échec, 0 test ignoré) |
| Compilation syntaxique | `python3 -m py_compile engine/*.py bootforge_ui/*.py bootforge_win/*.py packaging/*.py` | **Exit code 0** |
| Validation `.desktop` | `desktop-file-validate data/io.github.nicemank.BootForge.desktop` | **Exit code 0** |
| Packaging Linux `.deb` & `.tar.gz` | `bash packaging/build_deb.sh` + `dpkg-deb -c` | `bootforge_1.0.1_all.deb` (1,5 Mio) & `BootForge-1.0.1-linux.tar.gz` (1,5 Mio) générés avec `net_security.py` et `updater.py` |
| UI GTK4 hors écran | `BOOTFORGE_NO_STARTUP_UPDATE=1 xvfb-run -a ...` | **`GTK4_FINAL_OK`** (exit code 0) |
| UI Windows PyQt6 hors écran | `BOOTFORGE_NO_STARTUP_UPDATE=1 QT_QPA_PLATFORM=offscreen ...` | **`PYQT6_SMOKE_OK`** (exit code 0) |
| Détection matérielle réelle | `python3 engine/bootforge_engine.py devices` | `/dev/sdb` (VendorCo ProductCode 15.73 GB, `346d:5678`, S/N `6838251221392670722`, `is_system_disk: false`) vs `/dev/nvme0n1` et `/dev/sda` (`is_system_disk: true`) |
| Verrouillage disque système | `bootforge_engine.py run-job --dry-run` sur `/dev/nvme0n1` | **Exit code 2 (`SAFETY_BLOCKED`)** |

---

## 5. Non validé sans environnement / autorisation externe

1. **Tests destructifs sur clé USB réelle (`/dev/sdb`)** : flash direct ISO/DD, formatage GPT/MBR, installation Ventoy, écriture `badblocks -w`, restauration `.img` réelle. La partition `/dev/sdb1` contient actuellement `KALI LINUX` (`/run/media/niceman/KALI LINUX`). Aucune écriture réelle n'a été lancée sans votre accord explicite.
2. **Exécution Windows réelle** : la compilation PyInstaller/NSIS, UAC (`runas`), `diskpart`, `\\.\PhysicalDriveN`, et le lancement réel de l'installateur `.exe` ne peuvent pas être validés sur cet hôte Linux et devront être vérifiés sur Windows 10/11 ou via GitHub Actions (`windows-latest`).
