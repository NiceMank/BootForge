# BootForge 1.0.1 — Phase 4 : Validation Finale & Release Readiness

**Date :** 2026-11-14  
**Dépôt :** `/run/media/niceman/Nice/ProjetsWeb/BootForge` (`https://github.com/NiceMank/BootForge`)  
**Branche / Commit de référence :** `main` @ `5f25916` (+ modifications locales Phases 3 & 4 non committées)  
**Matériel USB utilisé (cible exclusive) :** `/dev/sdb` — `VendorCo ProductCode`, VID:PID `346d:5678`, série `6838251221392670722`, `15 728 640 000` octets (disques système `/dev/nvme0n1` et `/dev/sda` préservés et vérifiés intacts)  

---

## Décision finale : **NOT READY FOR RELEASE**

Le code cœur Linux, le découpage WIM/SWM, le diagnostic `P3-20`, le provisionnement cryptographique d'auto-update et l'amorçage Ventoy multi-ISO Linux en QEMU sont solidement établis, mais **la release publique n'est pas prête en l'état** pour les motifs objectifs suivants :

1. **Correctifs Phases 3 & 4 non committés et non validés sur CI `windows-latest` :** 7 fichiers modifiés (`bootforge_ui/backend.py`, `engine/bootforge_engine.py`, `engine/disk_utils.py`, `engine/ventoy_manager.py`, `engine/win_backend.py`, `engine/tests/test_phase2_regression.py`, `engine/tests/test_updater.py`) et les rapports Phase 3/4 ne sont pas encore poussés sur `main` ; le dernier run CI Windows (`43467152645`, commit `b2789b7`) ne couvre pas les 49 tests actuels ni ces correctifs.
2. **Validation PC Windows réel et boot PC physique non exécutés (`NOT TESTED`) :** l'exécution réelle de l'UAC, de `diskpart`, de `dism`, de `Ventoy2Disk.exe`, de `BootForge-1.0.1-win64-setup.exe` sur un vrai poste Windows et l'amorçage sur un véritable PC physique (UEFI/BIOS/Secure Boot) n'ont pas été testés.
3. **Amorçage Windows 11 SWM sous QEMU/OVMF incomplet (`PARTIAL`) :** QEMU/OVMF a chargé `Boot0002` jusqu'au microprogramme TianoCore / BDS, mais n'a pas atteint l'écran graphique WinPE Setup dans la fenêtre d'observation de 45 s.
4. **Incident I/O sur copie Ventoy multi-ISO Kali + historique du tag `v1.0.1` :** sur `/dev/sdb`, après ~16 Go d'écritures cumulées, le contrôleur USB a décroché (`[Errno 19] No such device`) au `sync()` final après Kali 4,46 Go (`last 1MB match: False` sur Kali, tandis que Arch Linux était intact SHA-256 et a booté en UEFI/BIOS). Par ailleurs, le tag `v1.0.1` déjà publié le 2026-11-08 ne contient pas `BOOTFORGE_MANIFEST.json`, ce qui impose de réserver la bascule Auto-Update opérationnelle à une future release signée (`v1.0.2+`).

---

## 1. Résolution du défaut P3-20 (Ventoy 1.1.18 MBR sans Secure Boot)

### Reproduction et mesures comparatives sur `/dev/sdb`

| Scénario exécuté sur `/dev/sdb` | Ventoy CLI `rc` | `fsck.vfat -n /dev/sdb2` | `FAT1 == FAT2` | Séquences LFN orphelines | Démarrage QEMU/OVMF |
|---|---:|---:|:---:|---:|---|
| Upstream `Ventoy2Disk.sh -I -S` (MBR, sans Secure Boot) | `0` | **`rc=1`** | `false` | 4 chaînes / 5 déclarées + SFN supprimés | **`Ventoy 1.1.18 UEFI` affiché (`1024x768`)** |
| BootForge `secure_boot=False` (MBR) | `0` | **`rc=1`** | Identique à `-S` | Identiques | — |
| Upstream `Ventoy2Disk.sh -I -s` (MBR, avec Secure Boot) | `0` | **`rc=0`** | `true` | `0` | — |

### Sortie exacte `fsck.vfat` et analyse FAT16 de `VTOYEFI` (`/dev/sdb2`)

`fsck.fat 4.2` sur `/dev/sdb2` (`1007` fichiers, `49580/64995` clusters, FAT16 254 secteurs/FAT) signale systématiquement après `Ventoy2Disk.sh -I -S` :
- `FATs differ but appear to be intact. Using first FAT.` (`fat1_equals_fat2: false`).
- `Orphaned long file name part "ENROLL_THIS_KEY_IN_MOKMANAGER.cer"` — entrée racine à l'offset `288` marquée supprimée (`\u00e5NROLL~1CER`, `0xE5`) avec `3` entrées LFN précédentes restées actives.
- Dans `EFI/BOOT/` :
  - `mmia32.efi` (`\u00e5MIA32  EFI`, offset `224`, `1` LFN précédente)
  - `grubia32_real.efi` (`\u00e5RUBIA~1EFI`, offset `448`, `2` LFN précédentes)
  - `mmx64.efi` (`\u00e5MX64   EFI`, offset `512`, `1` LFN précédente)
  - Plus les entrées SFN supprimées `\u00e5OOTIA32EFI` (offset `256`), `\u00e5RUBX6~1EFI` (offset `352`), `\u00e5OOTX64 EFI` (offset `544`) et `\u00e5BX64E~1SIG` (offset `704`, `2` LFN précédentes).
- Fichiers EFI actifs vérifiés intacts dans `EFI/BOOT/` : `BOOTX64.EFI` (`1 931 696` o), `BOOTIA32.EFI` (`1 296 816` o), `BOOTAA64.EFI` (`2 236 416` o), `BOOTMIPS.EFI` (`2 908 160` o), `FBX64.EFI` (`76 208` o).

### Vérification du démarrage UEFI et origine du problème

- Sous QEMU/OVMF (`OVMF_CODE_4M.fd`, capture `p3_20_upstream_nosb_ventoy_screen.png`), le périphérique boote proprement : `BdsDxe: starting Boot0002 "UEFI QEMU HARDDISK QM00001 "` et affiche le menu graphique **`Ventoy 1.1.18 UEFI`** (`non_black_ratio=1.0`).
- **Origine :** le défaut est **strictement upstream à Ventoy 1.1.18** : lorsque l'option `-S` (sans Secure Boot) est utilisée, l'outil upstream `vtoycli partresize -s` / nettoyage MOK/Shim supprime les entrées SFN des binaires Shim/MOK dans l'image FAT16 préconstruite de `VTOYEFI` sans purger toutes leurs entrées LFN associées ni resynchroniser FAT2 avec FAT1. En installation propre avec Secure Boot (`-s`), ce nettoyage n'a pas lieu et `fsck.vfat -n` retourne `rc=0`.
- **Décision :** BootForge ne modifie pas arbitrairement les fichiers EFI ou l'image `VTOYEFI` upstream. La configuration par défaut de BootForge (GPT + Secure Boot activé) n'est pas affectée (`fsck_rc=0`), et P3-20 est documenté comme une anomalie métadonnée FAT16 bénigne de Ventoy 1.1.18 en mode `-S`, sans impact sur le démarrage UEFI observé sous QEMU.

---

## 2. Validation WIM / SWM (ISO Windows officielle > 4 Go)

- **Outils installés :** `wimtools` `1.14.5-1+b2` (`wimlib-imagex`).
- **ISO testée :** `/media/Boy/Logiciels/Systeme/Win11_25H2_French_x64_v2.iso` (`8 473 616 384` o), contenant `sources/install.wim` de **`7 561 496 469` o** (> limite FAT32 de `4 294 967 295` o).
- **Résultats sur clé USB `/dev/sdb` (GPT + FAT32 `WIN11_SWM`) :**
  - `install.wim` absent sur la destination (`install_wim_present: false`).
  - Découpage SWM généré (3 segments tous sous 4 Gio, `under_fat32_limit: true`) :
    - `sources/install.swm` : `3 428 712 204` o
    - `sources/install2.swm` : `3 972 609 494` o
    - `sources/install3.swm` : `95 594 420` o
  - Intégrité système de fichiers FAT32 : `fsck.vfat -> rc=0` (`1169` fichiers).
  - Structure Windows Setup vérifiée présente : `setup.exe`, `bootmgr.efi`, `efi/boot/bootx64.efi`, `efi/microsoft/boot/bcd`, `sources/boot.wim`.
  - **Vérification `wimlib-imagex` :** `wimlib-imagex verify .../install.swm --ref=.../install*.swm` -> **`rc=0`** (`"install.swm" was successfully verified`, contrôle d'intégrité ignoré car non embarqué dans l'ISO source). *Note : un premier appel à `wimlib-imagex info` a renvoyé `rc=255` uniquement à cause d'un drapeau `--ref=` invalide pour la sous-commande `info` dans le script de test.*
  - **Gestion d'erreurs et interruptions :** dépassement disque simulé/contrôlé `ENOSPC -> rc=72` (`No space left on device`), interruption `SIGTERM -> rc=-15` avec nettoyage.
  - **Boot QEMU/OVMF :** `win11_swm_uefi` démarre `Boot0002` dans TianoCore, mais n'a pas atteint l'écran graphique WinPE Setup en 45 s (`PARTIAL`).

---

## 3. Validation QEMU / OVMF (vs PC physique)

**Environnement :** `qemu-system-x86` `1:11.1.2+ds-3`, `ovmf` `2026.08+ds-2` (`/usr/share/OVMF/OVMF_CODE_4M.fd`).  
**Correction BootForge vérifiée :** [`bootforge_ui/backend.py`](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_ui/backend.py#L335-L363) corrigé pour lancer OVMF avec `-drive if=pflash,format=raw,readonly=on,file=...` et `-enable-kvm -cpu host`, couvert par `test_qemu_preview_command_uses_pflash_for_split_ovmf_code`.

| Scénario testé sur `/dev/sdb` (`-snapshot=on`) | Résultat QEMU/OVMF | Preuves visuelles / console | Statut PC physique réel |
|---|---|---|---|
| **Arch Linux DD (BIOS Legacy)** — `archlinux-2026.07.01-x86_64.iso` (`1 583 022 080` o, `243,95 s`) | **PASS (QEMU)** | Menu ISOLINUX 6.04 (`640x480`), `Loading /arch/boot/x86_64/vmlinuz-linux... ok` + `initramfs-linux.img` | `NOT TESTED` |
| **Arch Linux DD (UEFI)** | **PARTIAL (QEMU)** | Menu `systemd-boot` affiché (`1280x800`), puis écran noir à +12 s après fin du décompte | `NOT TESTED` |
| **Ventoy 1.1.18 Multi-ISO UEFI** (`VENTOY_P4` GPT+SB, `ventoy.json` `225` o ; `fsck.exfat=0`, `fsck.vfat=0`) | **PASS (QEMU)** | Menu `Ventoy 1.1.18 UEFI` listant Arch + Kali, sélection Arch, mode normal, `systemd-boot` Arch (`Boot in 15s` -> `1s`) en console série | `NOT TESTED` |
| **Ventoy 1.1.18 Multi-ISO BIOS** | **PASS (QEMU)** | Menu `Ventoy 1.1.18 BIOS` listant Arch + Kali, sélection Arch, menu graphique ISOLINUX `Arch Linux install medium (x86_64, BIOS)` (`Automatic boot in 5 seconds...`) | `NOT TESTED` |
| **Windows 11 SWM UEFI** (`win11_swm_uefi`) | **PARTIAL (QEMU)** | `BdsDxe: starting Boot0002`, bloqué sur logo TianoCore/BDS dans les 45 s (WinPE GUI non atteint) | `NOT TESTED` |

**Note matérielle Multi-ISO :** lors de la copie séquentielle d'Arch Linux (`1,58 Go`) puis de Kali Linux (`4,46 Go`) après plusieurs cycles de flash intensifs sur `/dev/sdb`, `archlinux-2026.07.01-x86_64.iso` a été vérifié **identique octet par octet (SHA-256 `e86295dc0bdf9b85a5a9256810c553239689d2ae8e80eeec81b4e2e910d8a6c0`)**, tandis qu'une réinitialisation USB (`[Errno 19] No such device`) s'est produite au `sync()` final de Kali (`last 1MB match: False` sur Kali). Le nettoyage final par BootForge a restauré `/dev/sdb` en GPT + exFAT `BOOTFORGE_O` (`fsck.exfat rc=0`).

---

## 4. État de l'Auto-Update (`engine/updater.py` & `packaging/sign_release.py`)

- **Provisionnement cryptographique de production :**
  - Secret GitHub `BOOTFORGE_SIGNING_KEY` présent (`gh secret list`), clé privée stockée localement hors dépôt sous `~/.config/bootforge-secrets/release-2026.key` en permissions `0600`, aucun secret dans Git.
  - La clé publique dérivée correspond exactement à `TRUSTED_RELEASE_PUBLIC_KEYS["bootforge-release-2026"]` (`3f452d9cc024643b69eee6d72ddda3ec9d62c6ea9148a9f8564bb398e79c7469`) dans [`engine/updater.py`](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/engine/updater.py).
- **Contrôle sur l'API GitHub live :**
  - `check_for_update("1.0.1")` -> `up_to_date`.
  - `check_for_update("1.0.0")` -> `missing_manifest` (attendu : la release `v1.0.1` du 2026-11-08 est antérieure à `BOOTFORGE_MANIFEST.json[.sig]`). La chaîne live complète ne pourra être consommée par les clients qu'à partir de la prochaine release signée (`v1.0.2+`).
- **Couverture des tests de sécurité et d'exécution (`test_updater.py`, `test_phase2_regression.py` + simulation `sign_release.py --tag v1.2.0 --require-signature` sur les 4 canaux `linux_deb`, `linux_tar`, `windows_portable`, `windows_setup`) :**
  - Release signée valide (Ed25519 pur Python aligné sur `cryptography`) : **PASS**
  - Signature incorrecte / forgée : **PASS** (rejet fail-closed)
  - Manifeste modifié après signature : **PASS** (rejet)
  - Asset substitué ou hash SHA-256 incorrect : **PASS** (rejet + suppression `.part`)
  - Version rétrogradée (anti-downgrade `SemVer`) & usurpation URL : **PASS** (rejet)
  - Téléchargement interrompu / annulé / dépassement `max_bytes` : **PASS**
  - Installation Linux (`.deb` / `.tar.gz` avec vérification SHA-256 et chemins sûrs) : **PASS** (test unitaire/simulé)
  - Gestion de l'installateur Windows (`windows_setup` / `windows_portable`) : **PASS en tests unitaires mockés** (`ShellExecuteExW` / élévation non exécutés sur PC Windows réel : `NOT TESTED` en conditions réelles)
  - Report pendant une opération USB active (`_is_busy()` dans GTK4 [`bootforge_ui/widgets.py`](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_ui/widgets.py#L745-L842) et PyQt6 [`bootforge_win/dialogs.py`](file:///run/media/niceman/Nice/ProjetsWeb/BootForge/bootforge_win/dialogs.py#L605-L702)) : **PASS**

---

## 5. Résultats CI Windows (`windows-latest`) vs PC Windows réel

- **Dernier run CI GitHub Actions exécuté :** Run `43467152645` (`2026-11-13T16:52:57Z`, commit `b2789b7`) :
  - `Build Linux` : **PASS** (`46 passed`, smoke GTK4, artefacts `.deb` et `.tar.gz`).
  - `Build Windows` (`windows-latest`) : **PASS** (`43 passed, 3 skipped`, smoke PyQt6, `BootForge-1.0.1-win64-portable.zip` `37 985 637` o, `BootForge-1.0.1-win64-setup.exe` NSIS `27 131 538` o).
  - `Publish Official Multiplatform Release` : `skipped` (déclenchement sur tag `v*` uniquement).
- **Attention — dérive par rapport au workspace actuel :** le commit `HEAD` (`5f25916`) portait `[skip ci]` et les modifications locales actuelles des Phases 3 & 4 (`49` tests) n'ont **pas** encore été testées sur `windows-latest`.
- **Distinction CI vs PC Windows réel :** les tests CI `windows-latest` valident l'import PyQt6, la compilation PyInstaller/NSIS, les constructeurs d'arguments `diskpart`/`Ventoy2Disk.exe` et les tests unitaires mockés ; **UAC réel, `diskpart` destructif, `dism /Split-Image` réel, détection de périphériques physiques Windows, restauration USB Windows et exécution interactive de l'installateur / auto-update sur un véritable PC Windows restent `NOT TESTED`**.

---

## 6. Audit Final, Couverture & Régression

### Vérifications exécutées sur l'état final du dépôt

- **`pytest -v` (Linux Python 3.14.8) :** **`49 passed in 4.04s`** (0 échec, 0 test ignoré).
- **Compilation Python (`python3 -m compileall -q bootforge_ui bootforge_win engine packaging`) :** **`rc=0`**.
- **Validation desktop (`desktop-file-validate data/io.github.nicemank.BootForge.desktop`) :** **`rc=0`**.
- **Smokes UI offscreen :** GTK4 (`BOOTFORGE_NO_STARTUP_UPDATE=1 xvfb-run -a ...`) **`rc=0`** ; PyQt6 local offscreen **`OK`** (`PYQT6_LOCAL_SMOKE_OK`).
- **Analyse de sécurité statique (grep motifs dangereux) :** aucun `shell=True`, `yaml.load(`, `pickle.loads(`, `eval(` ou `mktemp(` détecté (seuls appels `.exec()` appartenant à GTK/Qt). `bandit` / `ruff` / `flake8` ne sont pas installés localement.
- **Cohérence versions & métadonnées :** `1.0.1` aligné dans `bootforge_ui/__init__.py`, `bootforge_win/__init__.py`, `engine/__init__.py`, `packaging/win_installer.nsi` et `README.md`.
- **Build Linux local (`bash packaging/build_deb.sh`) :**
  - `release/bootforge_1.0.1_all.deb` (`1 478 020` o, SHA-256 `1571b15e18106fc8db6aec993ce65bd15c2bcc69595c9315a2c9b5e73c0ebe28`)
  - `release/BootForge-1.0.1-linux.tar.gz` (`1 521 503` o, SHA-256 `7175e3bdce3ef6023ee475fd18bd421396eb75e7dd6703c03006be88bb27bb26`)
  - *Nettoyage mineur conseillé : anciens fichiers `1.0.0` encore présents localement dans `release/` (répertoire gitignoré).*

### Continuité des correctifs Phases 1–3

Tous les correctifs majeurs vérifiés présents dans le code et couverts par les tests : `UEFI_NTFS_SHA256`, `wimlib-imagex split ... 3800`, `dism /Split-Image ... /FileSize:3800`, `_safe_umount_cleanup`, `safe_extract_tar/zip` (`is_relative_to` + `filter="data"`), `_fetch_required_ventoy_sha256`, `validate_target_device`, garde `_is_busy()`, et `download_https_atomic`.

### Couverture actualisée (`trace.Trace` sur `pytest -q`, 49 tests)

| Module | Lignes exécutées / suivies | Couverture |
|---|---:|---:|
| `engine/unattend_builder.py` | `75 / 76` | **98,7 %** |
| `engine/net_security.py` | `97 / 111` | **87,4 %** |
| `engine/updater.py` | `297 / 367` | **80,9 %** |
| `engine/disk_utils.py` | `220 / 297` | **74,1 %** |
| `engine/i18n.py` | `72 / 110` | **65,5 %** |
| `engine/ventoy_manager.py` | `144 / 254` | **56,7 %** |
| `engine/win_backend.py` | `299 / 624` | **47,9 %** |
| `engine/catalog.py` | `7 / 23` | **30,4 %** |
| `engine/bootforge_engine.py` | `209 / 700` | **29,9 %** |
| `engine/iso_inspector.py` | `76 / 263` | **28,9 %** |

---

## 7. Matrice des fonctionnalités validées

| Domaine / Fonctionnalité | Statut | Preuve / Périmètre exact |
|---|---|---|
| Protection disques système & verrous cible (`/dev/nvme0n1`, `/dev/sda`, symlinks) | **PASS** | `49 passed`, tests matériels `/dev/sdb` sans incident système |
| P3-20 diagnostic Ventoy 1.1.18 MBR `-S` (`fsck.vfat rc=1`) | **PASS (Diagnostic)** | Reproduit 1:1 upstream `-S` vs BootForge ; défaut upstream documenté, boot UEFI QEMU OK |
| Découpage WIM/SWM (`install.wim` > 4 Go -> 3 `.swm` < 4 Go) & `wimlib verify` | **PASS** | `Win11_25H2_French_x64_v2.iso` -> `install*.swm`, `fsck.vfat=0`, `verify rc=0` |
| Gestion erreurs WIM/SWM (`ENOSPC` / `SIGTERM`) | **PASS** | `enospc_rc=72`, `sigterm_rc=-15` |
| Arch Linux DD BIOS (QEMU) | **PASS (QEMU)** | ISOLINUX 6.04 + chargement `vmlinuz-linux` & `initramfs-linux.img` |
| Arch Linux DD UEFI (QEMU) | **PARTIAL (QEMU)** | `systemd-boot` affiché, écran noir post-countdown |
| Ventoy Multi-ISO UEFI & BIOS (QEMU — Arch Linux) | **PASS (QEMU)** | Menu Ventoy 1.1.18 listant 2 ISOs + sélection & boot Arch ; Kali tronqué au `sync()` USB (`last 1MB match: False`) |
| Windows 11 SWM Boot UEFI (QEMU/OVMF) | **PARTIAL (QEMU)** | TianoCore `Boot0002` atteint, WinPE Setup non atteint en 45 s |
| Boot sur PC physique réel (UEFI / BIOS / Secure Boot) | **NOT TESTED** | Non testé hors émulation |
| Auto-Update Ed25519 (provisionnement, attaques, interruptions, report USB) | **PASS** | Clé `0600` + `BOOTFORGE_SIGNING_KEY` cohérents ; tests & simulation 4 canaux OK (`v1.0.1` live sans manifeste -> actif dès `v1.0.2+`) |
| Build Linux `.deb` / `.tar.gz` & UI GTK4 offscreen | **PASS** | `build_deb.sh` + `desktop-file-validate` + GTK4 smoke OK |
| CI Windows `windows-latest` sur **état courant Phases 3–4** | **NOT TESTED** | Dernier run PASS (`43467152645`) porte sur `b2789b7`, antérieur aux modifs locales |
| Exécution réelle sur PC Windows (UAC, `diskpart`, `dism`, `setup.exe`, auto-update) | **NOT TESTED** | Uniquement mocké en tests unitaires / CI |

---

## 8. Actions requises avant toute release publique (`v1.0.2` recommandée)

1. Committer et pousser les modifications Phases 3 & 4 (sans aucun secret) sur `main` afin d'obtenir un run vert complet sur GitHub Actions (`build-linux` + `build-windows` sur `windows-latest`).
2. Vérifier sur un PC Windows réel (ou VM Windows avec passthrough USB) le flash (`diskpart` / `dism` / UAC), la restauration et l'installateur `setup.exe`, ainsi que le boot d'une clé sur matériel physique.
3. Uniquement après validation explicite de Farel : créer et pousser un nouveau tag `v1.0.2` pour déclencher `publish-release` avec génération et signature Ed25519 (`--require-signature`) de `BOOTFORGE_MANIFEST.json[.sig]`.
