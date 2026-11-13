#!/usr/bin/env python3
"""Build script for Windows: runs PyInstaller to create portable directory & zip package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from bootforge_ui import __version__


def main() -> int:
    dist_dir = ROOT_DIR / "dist"
    build_dir = ROOT_DIR / "build" / "pyinstaller"
    release_dir = ROOT_DIR / "release"
    release_dir.mkdir(parents=True, exist_ok=True)

    sep = os.pathsep
    icon_path = str(ROOT_DIR / "assets" / "icon.ico")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "BootForge",
        "--hidden-import=engine.bootforge_engine",
        "--hidden-import=engine.win_backend",
        f"--icon={icon_path}",
        f"--add-data={ROOT_DIR / 'assets'}{sep}assets",
        f"--add-data={ROOT_DIR / 'engine'}{sep}engine",
        f"--add-data={ROOT_DIR / 'bootforge_win'}{sep}bootforge_win",
        "--workpath",
        str(build_dir),
        "--distpath",
        str(dist_dir),
        str(ROOT_DIR / "packaging" / "win_main.py"),
    ]

    print("Running PyInstaller command:")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        print(f"PyInstaller failed with code {proc.returncode}", file=sys.stderr)
        return proc.returncode

    app_dist = dist_dir / "BootForge"
    if not app_dist.is_dir():
        print(f"Expected distribution directory not found: {app_dist}", file=sys.stderr)
        return 1

    # Copy LICENSE and README into dist
    shutil.copy2(ROOT_DIR / "LICENSE", app_dist / "LICENSE.txt")
    shutil.copy2(ROOT_DIR / "README.md", app_dist / "README.txt")

    # Create portable zip
    zip_name = f"BootForge-{__version__}-win64-portable.zip"
    zip_path = release_dir / zip_name
    print(f"Creating portable zip archive: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(app_dist):
            for fn in files:
                fpath = Path(root) / fn
                arcname = fpath.relative_to(dist_dir)
                zf.write(fpath, arcname)

    print(f"Portable archive created successfully: {zip_path} ({zip_path.stat().st_size / (1024*1024):.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
