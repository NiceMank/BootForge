# BootForge — Conception du Système de Mise à Jour Automatique Sécurisé

**Date :** 2026-11-13  
**Cible :** `engine/updater.py`, `packaging/sign_release.py`, `bootforge_ui/`, `bootforge_win/`, `.github/workflows/ci-release.yml`  
**Dépôt officiel :** `https://github.com/NiceMank/BootForge`

---

## 1. Objectifs de sécurité

1. **Aucune exécution de binaire non vérifié (Fail-Closed)** : Un binaire ou paquet téléchargé ne peut jamais être exécuté ou extrait sur la seule base d'une URL GitHub ou d'un tag HTTP.
2. **Authentification cryptographique Ed25519 (RFC 8032)** : Le manifeste de release (`BOOTFORGE_MANIFEST.json`) est accompagné d'une signature détachée (`BOOTFORGE_MANIFEST.json.sig`) vérifiée hors ligne via une clé publique Ed25519 épinglée dans `engine/updater.py`.
3. **Zéro dépendance externe au runtime** : La vérification Ed25519 est implémentée en Python pur (RFC 8032) dans `engine/updater.py` afin que ni le paquet `.deb` (`python3-gi`) ni le binaire Windows (`PyQt6`) n'aient besoin de dépendances supplémentaires.
4. **Intégrité SHA-256 + Anti-TOCTOU** : Chaque asset listé dans `BOOTFORGE_MANIFEST.json` possède un condensat SHA-256 et une taille attendue. Le téléchargement utilise `download_https_atomic` (`.part` + `fsync` + `os.replace`) et le SHA-256 est revérifié juste avant lancement de l'installateur.
5. **Transport strict HTTPS + Allowlist** : Requêtes limitées à `GITHUB_ALLOWED_HOSTS` (`api.github.com`, `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, etc.), TLS >= 1.2, blocage de toute redirection HTTP ou vers domaine tiers.
6. **Protection Anti-Downgrade (SemVer)** : Toute version inférieure ou égale à la version courante (`__version__`) est rejetée, ainsi que toute divergence entre `manifest.version` / `manifest.tag` et le `tag_name` GitHub.
7. **Report automatique pendant les travaux USB** : L'installation est strictement bloquée tant qu'une écriture disque, un formatage, un Ventoy ou un benchmark est actif.

---

## 2. Format du manifeste signé (`BOOTFORGE_MANIFEST.json`)

```json
{
  "schema_version": 1,
  "product": "BootForge",
  "repo": "NiceMank/BootForge",
  "key_id": "bootforge-release-2026",
  "version": "1.0.2",
  "tag": "v1.0.2",
  "published_at": "2026-11-13T16:00:00Z",
  "assets": {
    "linux_deb": {
      "name": "bootforge_1.0.2_all.deb",
      "url": "https://github.com/NiceMank/BootForge/releases/download/v1.0.2/bootforge_1.0.2_all.deb",
      "sha256": "<64-hex>",
      "size": 123456
    },
    "linux_tar": { "...": "..." },
    "windows_setup": { "...": "..." },
    "windows_portable": { "...": "..." }
  }
}
```

La signature détachée `BOOTFORGE_MANIFEST.json.sig` contient la signature Ed25519 (64 octets encodés en Base64 standard) calculée sur les **octets UTF-8 exacts** du fichier `BOOTFORGE_MANIFEST.json`.

---

## 3. Matrice d'installation par plateforme

| Plateforme / Contexte | Asset sélectionné | Méthode d'application | Privilèges |
| :--- | :--- | :--- | :--- |
| **Linux (paquet `.deb` installé)** | `linux_deb` (`*.deb`) | `pkexec apt-get install -y <deb>` (repli `dpkg -i`) après validation utilisateur | Polkit (`pkexec`) |
| **Linux (portable / sources)** | `linux_tar` (`*-linux.tar.gz`) | Extraction sécurisée (`safe_extract_tar`) vers sous-dossier versionné du cache utilisateur | Utilisateur courant |
| **Windows (installateur NSIS)** | `windows_setup` (`*-win64-setup.exe`) | Exécution UAC via `ShellExecuteW(..., "runas", ...)` puis fermeture propre de BootForge | UAC Windows |
| **Windows (archive portable)** | `windows_portable` (`*-win64-portable.zip`) | Extraction sécurisée (`safe_extract_zip`) vers dossier temporaire + script PowerShell asynchrone attendant la fin du PID avant remplacement et relance | Utilisateur courant (ou dossier courant) |

---

## 4. Provisionnement et rotation de la clé Ed25519

Par défaut, avant le premier provisionnement par le mainteneur, `TRUSTED_RELEASE_PUBLIC_KEYS["bootforge-release-2026"]` est vide (`""`). Dans cet état, le vérificateur fonctionne en **fail-closed** (`status="unprovisioned_key"`) et refuse toute installation automatique.

Procédure pour activer la signature en production :
1. Générer la paire de clés localement :
   ```bash
   python3 packaging/sign_release.py --keygen
   ```
2. Copier la clé publique hexadécimale (64 caractères) dans `TRUSTED_RELEASE_PUBLIC_KEYS["bootforge-release-2026"]` de `engine/updater.py`.
3. Enregistrer la clé privée Base64/hex dans le secret GitHub Actions `BOOTFORGE_SIGNING_KEY` du dépôt `NiceMank/BootForge`.
4. Lors des releases taguées `v*`, le workflow `.github/workflows/ci-release.yml` signe automatiquement `BOOTFORGE_MANIFEST.json` -> `BOOTFORGE_MANIFEST.json.sig` et publie les deux fichiers dans les assets de la release.
