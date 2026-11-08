#!/usr/bin/env python3
"""Ventoy download/cache manager, ventoy.json visual model builder, and persistence image creator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

SAFE_ISO_RELPATH_RE = re.compile(r"^/[A-Za-z0-9._/ -]+$")
SAFE_SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SUPPORTED_IMAGE_EXTS = (".iso", ".wim", ".img", ".vhd", ".vhdx", ".efi")


def get_ventoy_cache_dir() -> Path:
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local_app) / "BootForge" / "ventoy"
    elif hasattr(os, "geteuid") and os.geteuid() == 0:
        base = Path("/var/cache/bootforge/ventoy")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bootforge" / "ventoy"
    base.mkdir(parents=True, exist_ok=True)
    return base


def find_cached_ventoy_dir() -> Optional[Path]:
    """Locate an already-extracted Ventoy Linux directory in cache."""
    cache = get_ventoy_cache_dir()
    for entry in sorted(cache.iterdir(), reverse=True):
        if entry.is_dir() and (entry / "Ventoy2Disk.sh").is_file() and (entry / "boot" / "boot.img").is_file():
            return entry
    return None


def find_cached_ventoy_windows_dir() -> Optional[Path]:
    """Locate an already-extracted Ventoy Windows directory in cache."""
    cache = get_ventoy_cache_dir()
    for entry in sorted(cache.iterdir(), reverse=True):
        if entry.is_dir() and (entry / "Ventoy2Disk.exe").is_file():
            return entry
    return None


def build_ventoy_windows_cli_args(
    action: str,
    disk_number: int,
    scheme: str = "gpt",
    secure_boot: bool = True,
    reserve_mb: int = 0,
) -> List[str]:
    """Build exact command-line arguments for Ventoy2Disk.exe VTOYCLI."""
    args = ["VTOYCLI"]
    if action == "ventoy_update":
        args.extend(["/U", f"/PhyDrive:{disk_number}"])
        if not secure_boot:
            args.append("/NOSB")
        return args

    args.extend(["/I", f"/PhyDrive:{disk_number}"])
    if str(scheme).lower() == "gpt":
        args.append("/GPT")
    if not secure_boot:
        args.append("/NOSB")
    if int(reserve_mb or 0) > 0:
        args.append(f"/R:{int(reserve_mb)}")
    return args


def download_and_extract_ventoy_windows(progress_cb=None) -> Path:
    """Fetch latest Ventoy Windows release zip from GitHub, verify SHA-256, and extract safely."""
    existing = find_cached_ventoy_windows_dir()
    if existing:
        return existing

    cache = get_ventoy_cache_dir()
    api_url = "https://api.github.com/repos/ventoy/Ventoy/releases/latest"
    req = urllib.request.Request(api_url, headers={"User-Agent": "BootForge/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        release = json.loads(resp.read().decode("utf-8"))

    assets = release.get("assets", [])
    zip_asset = next((a for a in assets if str(a.get("name", "")).endswith("-windows.zip")), None)
    sha_asset = next((a for a in assets if "sha256" in str(a.get("name", "")).lower()), None)

    if not zip_asset:
        raise RuntimeError("Archive Ventoy Windows introuvable dans la release GitHub.")

    zip_url = zip_asset["browser_download_url"]
    zip_name = os.path.basename(zip_asset["name"])
    zip_path = cache / zip_name
    total_size = int(zip_asset.get("size") or 0)

    expected_sha256: Optional[str] = None
    if sha_asset:
        try:
            sha_req = urllib.request.Request(sha_asset["browser_download_url"], headers={"User-Agent": "BootForge/1.0"})
            with urllib.request.urlopen(sha_req, timeout=15) as sresp:
                sha_text = sresp.read().decode("utf-8", errors="ignore")
            for line in sha_text.splitlines():
                if zip_name in line:
                    token = line.strip().split()[0].lower()
                    if re.match(r"^[0-9a-f]{64}$", token):
                        expected_sha256 = token
                        break
        except Exception:
            expected_sha256 = None

    dl_req = urllib.request.Request(zip_url, headers={"User-Agent": "BootForge/1.0"})
    hasher = hashlib.sha256()
    downloaded = 0
    with urllib.request.urlopen(dl_req, timeout=60) as dresp, open(zip_path, "wb") as out:
        while True:
            chunk = dresp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            hasher.update(chunk)
            downloaded += len(chunk)
            if progress_cb and total_size > 0:
                progress_cb(int(downloaded * 100 / total_size), f"Téléchargement Ventoy Windows ({downloaded // (1024*1024)} Mo)...")

    if expected_sha256 and hasher.hexdigest().lower() != expected_sha256:
        zip_path.unlink(missing_ok=True)
        raise ValueError("Échec de vérification SHA-256 de l'archive Ventoy Windows.")

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = (cache / member.filename).resolve()
            if not str(member_path).startswith(str(cache.resolve())):
                raise ValueError(f"Chemin non sûr dans l'archive Ventoy : {member.filename}")
        zf.extractall(cache)

    extracted = find_cached_ventoy_windows_dir()
    if not extracted:
        raise RuntimeError("L'extraction de Ventoy Windows n'a pas produit de dossier valide.")
    return extracted


def download_and_extract_ventoy(progress_cb=None) -> Path:
    """Fetch latest Ventoy Linux release tarball from GitHub, verify SHA-256 when available, and extract."""
    existing = find_cached_ventoy_dir()
    if existing:
        return existing

    cache = get_ventoy_cache_dir()
    api_url = "https://api.github.com/repos/ventoy/Ventoy/releases/latest"
    req = urllib.request.Request(api_url, headers={"User-Agent": "BootForge/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        release = json.loads(resp.read().decode("utf-8"))

    assets = release.get("assets", [])
    tar_asset = next((a for a in assets if str(a.get("name", "")).endswith("-linux.tar.gz")), None)
    sha_asset = next((a for a in assets if "sha256" in str(a.get("name", "")).lower()), None)

    if not tar_asset:
        raise RuntimeError("Archive Ventoy Linux introuvable dans la release GitHub.")

    tar_url = tar_asset["browser_download_url"]
    tar_name = os.path.basename(tar_asset["name"])
    tar_path = cache / tar_name
    total_size = int(tar_asset.get("size") or 0)

    expected_sha256: Optional[str] = None
    if sha_asset:
        try:
            sha_req = urllib.request.Request(sha_asset["browser_download_url"], headers={"User-Agent": "BootForge/1.0"})
            with urllib.request.urlopen(sha_req, timeout=15) as sresp:
                sha_text = sresp.read().decode("utf-8", errors="ignore")
            for line in sha_text.splitlines():
                if tar_name in line:
                    token = line.strip().split()[0].lower()
                    if re.match(r"^[0-9a-f]{64}$", token):
                        expected_sha256 = token
                        break
        except Exception:
            expected_sha256 = None

    dl_req = urllib.request.Request(tar_url, headers={"User-Agent": "BootForge/1.0"})
    hasher = hashlib.sha256()
    downloaded = 0
    with urllib.request.urlopen(dl_req, timeout=60) as dresp, open(tar_path, "wb") as out:
        while True:
            chunk = dresp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            hasher.update(chunk)
            downloaded += len(chunk)
            if progress_cb and total_size > 0:
                progress_cb(int(downloaded * 100 / total_size), f"Téléchargement Ventoy ({downloaded // (1024*1024)} Mo)...")

    if expected_sha256 and hasher.hexdigest().lower() != expected_sha256:
        tar_path.unlink(missing_ok=True)
        raise ValueError("Échec de vérification SHA-256 de l'archive Ventoy téléchargée.")

    # Safe extraction preventing path traversal
    with tarfile.open(tar_path, "r:gz") as tf:
        for member in tf.getmembers():
            member_path = (cache / member.name).resolve()
            if not str(member_path).startswith(str(cache.resolve())):
                raise ValueError(f"Chemin non sûr dans l'archive Ventoy : {member.name}")
        tf.extractall(cache)

    extracted = find_cached_ventoy_dir()
    if not extracted:
        raise RuntimeError("L'extraction de Ventoy n'a pas produit de dossier valide.")
    return extracted


def normalize_ventoy_image_path(rel_path: str) -> str:
    clean = "/" + rel_path.strip().lstrip("/")
    if ".." in clean.split("/") or not SAFE_ISO_RELPATH_RE.match(clean):
        raise ValueError(f"Chemin relatif Ventoy invalide : {rel_path!r}")
    return clean


def build_ventoy_json(config: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a clean, valid ventoy.json structure from UI settings."""
    win11_bypass = bool(config.get("win11_bypass_check", True))
    win11_nro = bool(config.get("win11_bypass_nro", True))
    tree_view = bool(config.get("tree_view", False))
    filter_hidden = bool(config.get("filter_dot_files", True))
    timeout = int(config.get("menu_timeout", 0) or 0)
    default_image = str(config.get("default_image") or "").strip()

    control: List[Dict[str, str]] = [
        {"VTOY_WIN11_BYPASS_CHECK": "1" if win11_bypass else "0"},
        {"VTOY_WIN11_BYPASS_NRO": "1" if win11_nro else "0"},
        {"VTOY_DEFAULT_MENU_MODE": "1" if tree_view else "0"},
        {"VTOY_FILT_DOT_UNDERSCORE_FILE": "1" if filter_hidden else "0"},
    ]
    if timeout > 0:
        control.append({"VTOY_MENU_TIMEOUT": str(min(timeout, 300))})
    if default_image:
        control.append({"VTOY_DEFAULT_IMAGE": normalize_ventoy_image_path(default_image)})

    doc: Dict[str, Any] = {"control": control}

    # Menu Aliases
    aliases = []
    for item in config.get("menu_alias") or []:
        img = str(item.get("image") or "").strip()
        alias = str(item.get("alias") or "").strip()
        if img and alias:
            aliases.append({"image": normalize_ventoy_image_path(img), "alias": alias[:80]})
    if aliases:
        doc["menu_alias"] = aliases

    # Auto Install (Windows autounattend.xml per ISO)
    auto_installs = []
    for item in config.get("auto_install") or []:
        img = str(item.get("image") or "").strip()
        tpl = str(item.get("template") or "").strip()
        if img and tpl:
            auto_installs.append(
                {
                    "image": normalize_ventoy_image_path(img),
                    "template": normalize_ventoy_image_path(tpl),
                }
            )
    if auto_installs:
        doc["auto_install"] = auto_installs

    # Persistence (.dat files per Linux ISO)
    persistences = []
    for item in config.get("persistence") or []:
        img = str(item.get("image") or "").strip()
        backend = str(item.get("backend") or "").strip()
        if img and backend:
            persistences.append(
                {
                    "image": normalize_ventoy_image_path(img),
                    "backend": normalize_ventoy_image_path(backend),
                }
            )
    if persistences:
        doc["persistence"] = persistences

    # File/Driver Injection per ISO
    injections = []
    for item in config.get("injection") or []:
        img = str(item.get("image") or "").strip()
        archive = str(item.get("archive") or "").strip()
        if img and archive:
            injections.append(
                {
                    "image": normalize_ventoy_image_path(img),
                    "archive": normalize_ventoy_image_path(archive),
                }
            )
    if injections:
        doc["injection"] = injections

    return doc


def read_ventoy_partition_state(mount_point: str) -> Dict[str, Any]:
    """Inspect a mounted Ventoy data partition: ISOs, ventoy.json, persistence files, scripts."""
    root = Path(mount_point)
    if not root.is_dir():
        raise FileNotFoundError(f"Point de montage introuvable : {mount_point}")

    images: List[Dict[str, Any]] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        rel_parts = item.relative_to(root).parts
        # Skip internal ventoy folder and hidden/System Volume Information
        if rel_parts[0].lower() in ("ventoy", "system volume information", "$recycle.bin"):
            continue
        if item.name.startswith("._"):
            continue
        if item.suffix.lower() in SUPPORTED_IMAGE_EXTS:
            rel_str = "/" + "/".join(rel_parts)
            images.append(
                {
                    "name": item.name,
                    "rel_path": rel_str,
                    "size_bytes": item.stat().st_size,
                    "os_hint": "windows" if "win" in item.name.lower() else "linux",
                }
            )

    ventoy_json_path = root / "ventoy" / "ventoy.json"
    ventoy_config: Dict[str, Any] = {}
    if ventoy_json_path.is_file():
        try:
            ventoy_config = json.loads(ventoy_json_path.read_text(encoding="utf-8"))
        except Exception:
            ventoy_config = {}

    persistence_files: List[Dict[str, Any]] = []
    p_dir = root / "ventoy" / "persistence"
    if p_dir.is_dir():
        for f in sorted(p_dir.glob("*.dat")):
            persistence_files.append({"name": f.name, "rel_path": f"/ventoy/persistence/{f.name}", "size_bytes": f.stat().st_size})

    # Also detect root-level .dat persistence files
    for f in sorted(root.glob("*.dat")):
        persistence_files.append({"name": f.name, "rel_path": f"/{f.name}", "size_bytes": f.stat().st_size})

    scripts: List[str] = []
    s_dir = root / "ventoy" / "scripts"
    if s_dir.is_dir():
        for f in sorted(s_dir.glob("*.xml")):
            scripts.append(f"/ventoy/scripts/{f.name}")

    usage = shutil.disk_usage(root)
    return {
        "mount_point": str(root),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "images": images,
        "ventoy_json": ventoy_config,
        "persistence_files": persistence_files,
        "scripts": scripts,
    }


def create_persistence_dat_image(
    output_path: str,
    size_mb: int,
    label: str = "casper-rw",
    with_persistence_conf: bool = False,
) -> None:
    """Create a real ext4 loop image (.dat) for Ventoy Linux persistence (requires root to mount for conf)."""
    size_mb_clamped = max(128, min(int(size_mb), 65536))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Create sparse file of exact size
    subprocess.run(["truncate", "-s", f"{size_mb_clamped}M", str(out)], check=True)
    # Format ext4 with the exact distribution label (casper-rw for Ubuntu, persistence for Debian/Kali)
    safe_lbl = re.sub(r"[^A-Za-z0-9_-]", "_", label)[:16] or "casper-rw"
    subprocess.run(["mkfs.ext4", "-F", "-q", "-L", safe_lbl, str(out)], check=True)

    if with_persistence_conf:
        tmp_mnt = tempfile.mkdtemp(prefix="bootforge_pconf_")
        try:
            subprocess.run(["mount", "-o", "loop", str(out), tmp_mnt], check=True)
            conf_file = Path(tmp_mnt) / "persistence.conf"
            conf_file.write_text("/ union\n", encoding="utf-8")
            os.sync()
        finally:
            subprocess.run(["umount", tmp_mnt], check=False)
            shutil.rmtree(tmp_mnt, ignore_errors=True)
