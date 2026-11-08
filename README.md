# BootForge

Utilitaire graphique Linux (GTK 4 / Libadwaita) permettant de créer des clés USB démarrables Windows ou Linux, ou de préparer une clé multi-systèmes avec Ventoy.

L'interface propose deux niveaux d'affichage activables à tout moment :
- **Mode Simple** (par défaut) : écran épuré en 3 étapes pour choisir un fichier `.iso`/`.img` et créer la clé avec les réglages adaptés automatiquement.
- **Mode Avancé** : accès complet aux paramètres de formatage (GPT/MBR, NTFS + UEFI:NTFS, FAT32 + découpe `install.wim`, mode DD, `autounattend.xml`, pilotes `$WinPEDriver$`, configuration `ventoy.json`, `badblocks`, QEMU/OVMF et journal des commandes).

## Fonctionnalités

### Création directe (compatible Rufus)
- Prise en charge des images Windows 10/11 et Linux hybrides (`.iso`, `.img`, `.raw`, `.img.gz`, `.img.xz`).
- Gestion des fichiers `install.wim` de plus de 4 Go via partition **NTFS + UEFI:NTFS** ou découpe automatique en FAT32 (`wimlib-imagex`).
- Mode d'écriture brute (DD) pour les images Linux hybrides.
- Création optionnelle du fichier `autounattend.xml` pour Windows 11 (TPM/Secure Boot/RAM, compte local hors ligne, BitLocker, langue) et dossier de pilotes `$WinPEDriver$`.
- Persistance Linux `ext4` (`casper-rw` pour Ubuntu/Mint ou `persistence` pour Debian/Kali).

### Mode multi-systèmes (Ventoy)
- Installation ou mise à jour non destructive de Ventoy sur la clé USB.
- Lecture d'une clé Ventoy existante et copie de plusieurs systèmes (Windows + Linux) sans tout effacer.
- Configuration graphique de `/ventoy/ventoy.json` (règles globales Windows 11, alias, image par défaut, fichiers `.dat` persistants, profils `autounattend.xml`).

### Outils et téléchargements
- Vérification d'intégrité (`SHA-256`, `SHA-1`, `MD5`).
- Test de vitesse de lecture USB et contrôle de surface (`badblocks`).
- Remise à zéro d'usine d'une clé USB (suppression de toutes les partitions et formatage complet).
- Sauvegarde `.img` / `.img.gz` et aperçu en machine virtuelle lecture seule (QEMU/OVMF).
- Téléchargement direct d'images officielles (Ubuntu, Linux Mint, Debian, SystemRescue, GParted Live, Kali Linux).

## Installation

### Paquet `.deb` (Debian / Ubuntu / Kali Linux)

```bash
sudo apt install ./bootforge_1.0.0_all.deb
```

### Lancer depuis les sources

Installez les paquets nécessaires puis lancez `./bootforge` :

```bash
sudo apt update && sudo apt install -y \
  python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  pkexec 7zip parted gdisk dosfstools ntfs-3g exfatprogs e2fsprogs util-linux udisks2
```

Dépendances facultatives (découpe WIM FAT32 et test QEMU) :

```bash
sudo apt install -y wimtools qemu-system-x86 ovmf mtools xorriso smartmontools
```

Exécution directe :

```bash
git clone https://github.com/NiceMank/BootForge.git
cd BootForge
./bootforge
```

## Tests et construction du paquet

```bash
python3 -m unittest discover -s engine/tests -v
bash packaging/build_deb.sh
```

Les paquets `.deb` et `.tar.gz` sont générés dans `release/`.

## Licence et crédits

Distribué sous licence **GNU GPL v3.0** (`LICENSE`).
- Chargeur `assets/uefi-ntfs/uefi-ntfs.img` issu du projet [Rufus](https://github.com/pbatard/rufus) (Pete Batard, GPLv3).
- Support multi-systèmes basé sur [Ventoy](https://github.com/ventoy/Ventoy) (longpanda, GPLv3).
