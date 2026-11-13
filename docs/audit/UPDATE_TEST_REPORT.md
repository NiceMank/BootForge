# BootForge — Rapport de Tests du Système d'Auto-Mise à Jour

**Date :** 2026-11-13  
**Modules testés :** `engine/updater.py`, `packaging/sign_release.py`, `bootforge_ui/widgets.py` (`UpdatesDialog`), `bootforge_win/dialogs.py` (`UpdatesWinDialog`)

---

## 1. Tests unitaires & cryptographiques (`engine/tests/test_updater.py`)

Exécution réelle observée : `5 passed` dans `pytest -v` (inclus dans la suite de 39 tests).

| Cas de test | Ce qui a été vérifié | Résultat |
| :--- | :--- | :--- |
| `test_pure_python_ed25519_matches_cryptography_and_rejects_forgeries` | Signatures Ed25519 réelles générées par `cryptography`, vérifiées par `ed25519_verify` en Python pur ; altération 1 bit signature/message, mauvaise clé publique, `S >= L`, point identité et mauvaises longueurs | **PASS** |
| `test_semver_precedence_and_anti_downgrade` | Comparaison `1.0.2-rc.1 < 1.0.2`, `1.0.2-alpha.1 < 1.0.2-beta.2`, rejet des tags non-SemVer | **PASS** |
| `test_verify_manifest_fail_closed_cases` | Rejet `UNPROVISIONED_KEY` si clé vide, validation nominale, rejet `INVALID_SIGNATURE` si manifeste altéré, rejet si champ `repo != NiceMank/BootForge` même avec signature valide | **PASS** |
| `test_sign_release_roundtrip_and_check_for_update_all_states` | Génération réelle de `BOOTFORGE_MANIFEST.json` + `.sig` via `packaging/sign_release.py`, sélection `linux_deb` et `windows_setup`, détection `up_to_date` et blocage `unprovisioned_key` | **PASS** |
| `test_download_deferral_toctou_guard_and_platform_apply` | Blocage automatique si `is_busy_fn() == True`, téléchargement atomique, revérification SHA-256 anti-TOCTOU avant exécution, invocation `pkexec` simulée pour `.deb`, extraction sûre `linux_tar` et `windows_portable` | **PASS** |

---

## 2. Vérification UI hors écran

- **Linux GTK4 (`xvfb-run`) :** Ouverture de `BootForgeWindow` + `UpdatesDialog` (avec `BOOTFORGE_NO_STARTUP_UPDATE=1`) → `GTK4_SMOKE_OK` (exit code 0).
- **Windows PyQt6 (`QT_QPA_PLATFORM=offscreen` sur hôte Linux) :** Ouverture de `BootForgeMainWindow` + `UpdatesWinDialog` + fermeture propre avec attente des `QThread` → `PYQT6_SMOKE_OK` (exit code 0).

> Note : Le lancement réel d'un installateur `.exe` sous UAC Windows (`ShellExecuteW(..., "runas", ...)`) et d'une mise à niveau `pkexec apt-get install` système en conditions réelles n'a pas été exécuté hors mock.
