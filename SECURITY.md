# Politique de sécurité

## Modèle de privilèges

BootForge sépare l'interface graphique des opérations privilégiées :
- L'interface GTK 4 (`bootforge_ui`) s'exécute sans droits `root`.
- Seules les actions d'écriture sur périphérique USB invoquent `pkexec` sur `engine/bootforge_engine.py run-job --job <fichier.json>`.
- Avant toute écriture, `validate_target_device()` re-vérifie via `lsblk -J` côté `root` que la cible est un support amovible et ne porte aucun montage système (`/`, `/boot`, `/boot/efi`, `/home`, `[SWAP]`).

## Signaler une faille

Si vous identifiez un problème de sécurité (élévation de privilège, chemin `/dev/*`, injection XML/JSON ou risque d'effacement disque) :
1. N'ouvrez pas d'issue publique avec un scénario destructeur.
2. Utilisez l'onglet **Security** (*Report a vulnerability*) du dépôt GitHub.
