# Contribuer à BootForge

Merci de votre aide pour améliorer BootForge.

## Prérequis locaux

```bash
sudo apt update && sudo apt install -y \
  python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  pkexec 7zip parted gdisk dosfstools ntfs-3g exfatprogs e2fsprogs util-linux udisks2 \
  wimtools qemu-system-x86 ovmf
```

## Développement et tests

```bash
# Lancer l'interface depuis le dépôt
./bootforge

# Exécuter les tests unitaires
python3 -m unittest discover -v

# Construire le paquet .deb dans release/
bash packaging/build_deb.sh
```

## Conventions

1. **Aucune exécution shell** : toutes les commandes externes doivent passer des listes d'arguments avec `shell=False`.
2. **Protection du disque système** : toute écriture disque doit passer par `validate_target_device()` (`engine/disk_utils.py`).
3. **Validation stricte** : tout contenu écrit dans `autounattend.xml` ou `ventoy.json` doit être validé et échappé proprement.
4. **Tests au vert** : vérifiez la suite `unittest` avant de proposer une pull request.
