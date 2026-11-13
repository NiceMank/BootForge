# Politique de sécurité

## Modèle de privilèges

BootForge sépare l'interface graphique des opérations privilégiées :
- L'interface GTK 4 (`bootforge_ui`) s'exécute sans droits `root`.
- Seules les actions d'écriture sur périphérique USB invoquent `pkexec` sur `engine/bootforge_engine.py run-job --job <fichier.json>`.
- Avant toute écriture, `validate_target_device()` résout les liens symboliques (`/dev/disk/by-*`) et re-vérifie via `lsblk -J` (y compris sur volumes LUKS/LVM imbriqués) que la cible est un support amovible et ne porte aucun montage système (`/`, `/boot`, `/boot/efi`, `/var`, `/usr`, `/etc`, `/home`, `[SWAP]`, etc.).
- `validate_paths_not_on_device()` interdit d'utiliser un fichier ISO ou une destination situés sur une partition du périphérique cible.

## Sécurité réseau, archives et mises à jour

- Tous les téléchargements réseau passent par `engine/net_security.py` (TLS >= 1.2 strict, blocage de toute redirection rétrogradant en HTTP, vérification d'hôtes autorisés et écriture atomique `.part` + `fsync` + `os.replace`).
- Le vérificateur de mises à jour (`engine/updater.py`) vérifie une signature détachée **Ed25519** (RFC 8032 en Python pur) sur `BOOTFORGE_MANIFEST.json`, applique une protection **anti-downgrade SemVer** et revérifie le **SHA-256** avant toute installation (mode **fail-closed** si la clé publique n'est pas provisionnée).
- Les archives Ventoy et mises à jour portables sont extraites de manière sûre (`safe_extract_zip` / `safe_extract_tar`) en bloquant path traversal, préfixes frères, liens symboliques/durs et nœuds spéciaux.

## Signaler une faille

Si vous identifiez un problème de sécurité (élévation de privilège, chemin `/dev/*`, injection XML/JSON ou risque d'effacement disque) :
1. N'ouvrez pas d'issue publique avec un scénario destructeur.
2. Utilisez l'onglet **Security** (*Report a vulnerability*) du dépôt GitHub.
