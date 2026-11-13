# Rapport de Tests Matériels USB — Périphérique Réel (Non Destructifs Uniquement)

Date : 2026-11-13  
Statut sécurité : **AUCUNE OPÉRATION DESTRUCTIVE EXÉCUTÉE** (conformément au protocole strict de protection des données).

---

## 1. Phase 1 & 2 : Identification et Distinction Matérielle (Linux)

Inspection réalisée via `lsblk -J -b -p`, `lsblk -f`, `lsusb`, `udevadm info`, `findmnt` et `/proc/mounts`.

### A. Disques système / internes (VERROUILLÉS par BootForge)

1. **`/dev/nvme0n1`** — Disque OS principal
   - Modèle : `INTEL SSDPEKNW512G8H` · Capacité : `512 110 190 592` octets (512,11 Go) · Série : `BTNH10350Y3M512A` · Transport : `nvme` (`rm=false`, `hotplug=false`).
   - Partitions montées actives : `nvme0n1p1` (`/boot/efi`), `nvme0n1p3` (`/media/Boy`), `nvme0n1p5` (`/media/Nice`, dépôt courant), `nvme0n1p7` (`/`), `nvme0n1p8` (`[SWAP]`), `nvme0n1p9` (`/home`).
   - Verdict BootForge (`devices`) : `is_system_disk: true` (5 raisons détectées).
2. **`/dev/sda`** — SSD interne secondaire
   - Modèle : `HP SSD S700 250GB` (`ATA`) · Capacité : `250 059 350 016` octets (250,06 Go) · Série : `HBSA29295400154` · Transport : `sata` (`rm=false`, `hotplug=false`).
   - Partitions : `sda1`–`sda5` (vfat + ntfs Windows, non montées).
   - Verdict BootForge (`devices`) : `is_system_disk: true` (`Disque interne non-USB (sata)`).

### B. Clé USB physique connectée (Périphérique cible identifié sans ambiguïté)

- **Chemin bloc** : `/dev/sdb` (`DEVLINKS`: `/dev/disk/by-id/usb-VendorCo_ProductCode_6838251221392670722-0:0`)
- **Fabricant / Modèle** : `VendorCo` / `ProductCode` (`lsusb`: `Bus 001 Device 002: ID 346d:5678 USB Disk 2.0`)
- **Numéro de série** : `6838251221392670722`
- **Capacité** : `15 728 640 000` octets (15,73 Go / 14,65 GiB)
- **Bus / Vitesse** : USB 2.0 (`usb_speed_mbps: 480`, `vid_pid: 346d:5678`, `removable: true`, `hotplug: true`, `read_only: false`)
- **Table de partitions** : GPT (`ID_PART_TABLE_UUID: eac693c5-60ac-4c4e-bd02-be1fcb31727b`)
- **Partition 1** : `/dev/sdb1` (`15 727 566 848` octets), `vfat` (FAT32), label `"KALI LINUX"`, UUID `9E02-153C`, montée en lecture/écriture sous `/run/media/niceman/KALI LINUX` (contient une arborescence Kali Linux installée : `boot/`, `EFI/`, `pool/`, `md5sum.txt`, etc.).
- **Verdict BootForge (`devices`)** : `is_system_disk: false`, `ventoy_detected: false`, informations modèle, taille, série, VID:PID, vitesse USB, partition et point de montage **100 % concordantes** avec `lsblk` et `udevadm`.

---

## 2. Phase 3 : Résultats des Tests Non Destructifs Réels

| Test | Méthode exécutée | Résultat observé | Statut |
| :--- | :--- | :--- | :--- |
| Détection moteur (`devices`) | `python3 engine/bootforge_engine.py devices` | `/dev/sdb` détecté avec capacité `15728640000`, `vid_pid: 346d:5678`, `usb_speed_mbps: 480`, `sdb1` montée. | **PASS** |
| Détection UI GTK4 réelle | Lancement `BootForgeWindow` sous `xvfb-run -a` | 3 disques inventoriés, sélection automatique sur `/dev/sdb` (`ProductCode`), disques système masqués par défaut. | **PASS** |
| Verrouillage disques système (`--dry-run`) | `run-job` `usb_wipe_reset` sur `/dev/sda` et `/dev/nvme0n1` | Code retour `2`, erreur `SAFETY_BLOCKED` explicite sur les deux disques internes. | **PASS** |
| Validation cible USB | `validate_target_device("/dev/sdb")` | Retourne `BlockDeviceInfo` valide pour `VendorCo ProductCode`. *(Anomalie mineure relevée : `vid_pid` non peuplé directement via l'appel par défaut, corrigée en Phase 5.)* | **PASS (écart mineur)** |
| Hachage SHA-256 réel sur clé USB | `bootforge_engine.py hash-file "/run/media/niceman/KALI LINUX/md5sum.txt"` vs `sha256sum` | SHA-256 identique : `d6a5954d7204464607d60f11666b25581c6c168c9f83921dd4ba7322107bf244` (396 176 octets). | **PASS** |
| Inspection d'image réelle sur USB | `bootforge_engine.py inspect-iso "/run/media/niceman/KALI LINUX/boot/grub/efi.img"` | Détecté comme image brute (`format: raw`, `4259840` octets), mode DD recommandé. | **PASS** |
| Gestion permissions brutes sans root | Lecture directe `/dev/sdb` en utilisateur `niceman` (non membre du groupe `disk`) | `PermissionError: [Errno 13] Permission denied` proprement remonté (accès `brw-rw---- root:disk` intact). | **PASS** |
| Simulation plans d'opérations (`--dry-run`) sur `/dev/sdb` | `flash_direct` (ISO + persistance), `ventoy_install`, `usb_wipe_reset`, `usb_benchmark`, `usb_badblocks`, `usb_backup_img` | Tous retournent `exit_code: 0` avec plan de partitions cohérent et 0 écriture disque. | **PASS** |
| Benchmark lecture / `badblocks` brut `/dev/sdb` | Nécessite lecture brute `/dev/sdb` (`root` via `pkexec`) | Non exécuté en tant que root pendant cette passe sans interaction polkit ; vérifié côté droits et `--dry-run`. | **BLOQUÉ (privilèges root interactifs)** |

---

## 3. Phase 4 : Tests Destructifs Contrôlés (En attente d'autorisation)

La clé `/dev/sdb` (`VendorCo ProductCode`, 15,73 Go, `KALI LINUX`) contient des fichiers existants. Conformément au protocole :
- **Aucune opération de flash, formatage, remise à zéro, installation Ventoy, badblocks `-w` ou clonage inverse n'a été exécutée sur `/dev/sdb`.**
- Une demande d'autorisation explicite avec identification complète de `/dev/sdb` et avertissement de perte totale de données sera présentée à l'utilisateur en fin d'audit si des tests destructifs réels doivent être menés.
