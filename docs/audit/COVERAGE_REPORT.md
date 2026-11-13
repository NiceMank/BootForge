# BootForge — Rapport de Couverture des Tests

**Date :** 2026-11-13  
**Suite :** `pytest -v` (`39 passed in 5.88s`, `engine/tests/`)  
**Méthode de mesure :** Module standard Python `trace` (`trace.Trace(count=1, trace=0)`) corrélé aux nœuds AST `ast.stmt` dans le processus de test principal.

---

## 1. Couverture par module (`engine/*.py`)

| Module | Instructions exécutées | Instructions totales | Couverture AST | Observations |
| :--- | ---: | ---: | ---: | :--- |
| `unattend_builder.py` | 73 | 74 | **98.6 %** | Génération XML Windows 11, échappement et variantes quasi intégralement couverts |
| `updater.py` | 247 | 326 | **75.8 %** | Ed25519 pur Python, SemVer, anti-downgrade, TOCTOU, sélection/application OS couverts |
| `net_security.py` | 74 | 103 | **71.8 %** | Validation HTTPS, allowlist hôtes, redirection anti-downgrade, écriture atomique `.part` |
| `disk_utils.py` | 193 | 280 | **68.9 %** | `lsblk`, LUKS/LVM imbriqués, blocage système, plans de partition, `validate_paths_not_on_device` |
| `i18n.py` | 62 | 100 | **62.0 %** | Détection langue, interpolation, formatage octets/débits FR & EN |
| `ventoy_manager.py` | 109 | 235 | **46.4 %** | Extraction sûre ZIP/TAR, `ventoy.json`, contrôle fail-closed SHA-256 |
| `iso_inspector.py` | 69 | 256 | **27.0 %** | Parsing ISO9660 pur Python, SHA-256/SHA-1/MD5 |
| `catalog.py` | 4 | 20 | **20.0 %** | Validation structure et URLs HTTPS du catalogue officiel |
| `win_backend.py` | 115 | 579 | **19.9 %** | `parse_windows_disks_json`, `validate_windows_target`, `diskpart`, PowerShell via `BF_ISO_PATH`, dry-run Windows |
| `bootforge_engine.py` | 45 | 659 | **6.8 %** | Remarque : `test_engine.py` lance `bootforge_engine.py` dans des sous-processus CLI (`subprocess`), non comptabilisés dans le trace du processus père ; les branches destructives réelles (formatage/écriture) restent hors test sans autorisation matérielle |
| **TOTAL (`engine/`)** | **991** | **2632** | **37.7 %** | **39/39 tests unitaires réussis** |

---

## 2. Synthèse et limites assumées

- Tous les modules critiques de sécurité (`unattend_builder.py`, `updater.py`, `net_security.py`, `disk_utils.py`) sont couverts à **69 %–99 %**.
- Les branches d'écriture physique destructives sous Linux (`mkfs`, `parted`, `dd` brut, `Ventoy2Disk`) et sous Windows (`CreateFileW` sur `\\.\PhysicalDriveN`, `diskpart`, UAC) ne sont pas exécutées en tant que tests automatisés sur matériel réel sans autorisation préalable.
- Les interfaces GTK4 (`bootforge_ui`) et PyQt6 (`bootforge_win`) sont vérifiées séparément par compilation `py_compile` et tests de fumée hors écran (`xvfb-run` et `QT_QPA_PLATFORM=offscreen`).
