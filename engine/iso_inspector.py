#!/usr/bin/env python3
"""ISO, IMG, VHD, and WIM inspector + streaming cryptographic hash verifier."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


SUPPORTED_EXTENSIONS = {
    ".iso",
    ".img",
    ".raw",
    ".vhd",
    ".vhdx",
    ".gz",
    ".xz",
    ".zst",
}


def inspect_image(image_path: str) -> Dict[str, Any]:
    """Inspect an ISO/IMG file safely without mounting, detecting OS, WIM size, and persistence."""
    real_path = os.path.realpath(image_path)
    p = Path(real_path)
    if not p.is_file():
        raise FileNotFoundError(f"Image introuvable : {image_path}")

    size_bytes = p.stat().st_size
    name_lower = p.name.lower()

    ext = p.suffix.lower()
    is_compressed_raw = name_lower.endswith((".img.gz", ".img.xz", ".img.zst", ".raw.gz", ".raw.xz"))
    is_raw = ext in (".img", ".raw", ".vhd", ".vhdx") or is_compressed_raw
    is_iso = ext == ".iso"

    info: Dict[str, Any] = {
        "path": real_path,
        "filename": p.name,
        "size_bytes": size_bytes,
        "format": "raw" if is_raw else ("iso" if is_iso else ext.lstrip(".")),
        "os_family": "unknown",
        "os_label": p.stem,
        "architecture": "x64" if ("x64" in name_lower or "amd64" in name_lower or "x86_64" in name_lower) else (
            "arm64" if ("arm64" in name_lower or "aarch64" in name_lower) else "unknown"
        ),
        "is_hybrid_boot": False,
        "has_uefi_boot": False,
        "install_wim_size": 0,
        "install_wim_path": "",
        "needs_uefi_ntfs": False,
        "recommended_scheme": "gpt",
        "recommended_fs": "fat32",
        "recommended_write_mode": "dd" if is_raw else "iso",
        "persistence_type": "none",
        "windows_editions": [],
        "notes": [],
    }

    if is_raw:
        info["os_family"] = "raw"
        info["recommended_write_mode"] = "dd"
        info["notes"].append("Image disque brute : écriture bloc par bloc (mode DD) recommandée.")
        return info

    # Inspect first 64 KiB for MBR / El Torito / ISO9660 hybrid signature
    try:
        with open(real_path, "rb") as f:
            head = f.read(65536)
        if len(head) >= 512 and head[510:512] == b"\x55\xaa":
            info["is_hybrid_boot"] = True
    except OSError:
        pass

    if is_iso and shutil.which("7z"):
        proc = subprocess.run(
            ["7z", "l", "-slt", real_path],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if proc.returncode == 0:
            entries = _parse_7z_slt(proc.stdout)
            _classify_iso_entries(entries, info)

    # Check WIM editions if wimlib-imagex is installed and Windows detected
    if info["os_family"] == "windows" and info["install_wim_path"] and shutil.which("wimlib-imagex"):
        info["notes"].append("wimlib-imagex disponible : découpe FAT32 possible.")

    return info


def _parse_7z_slt(output: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            if "Path" in current:
                entries.append(current)
            current = {}
            continue
        if " = " in stripped:
            k, v = stripped.split(" = ", 1)
            current[k.strip()] = v.strip()
    if "Path" in current:
        entries.append(current)
    return entries


def _classify_iso_entries(entries: List[Dict[str, str]], info: Dict[str, Any]) -> None:
    paths_lower: Dict[str, int] = {}
    for entry in entries:
        epath = entry.get("Path", "").replace("\\", "/")
        try:
            esize = int(entry.get("Size") or 0)
        except ValueError:
            esize = 0
        paths_lower[epath.lower()] = esize

    # Check UEFI bootloader presence
    if any(p.startswith("efi/boot/boot") and p.endswith(".efi") for p in paths_lower):
        info["has_uefi_boot"] = True

    # Windows detection
    win_wim = None
    for candidate in ("sources/install.wim", "sources/install.esd", "sources/install.swm", "x64/sources/install.wim"):
        if candidate in paths_lower:
            win_wim = candidate
            break

    if win_wim or ("sources/boot.wim" in paths_lower and "setup.exe" in paths_lower):
        info["os_family"] = "windows"
        wim_size = paths_lower.get(win_wim or "", 0)
        info["install_wim_size"] = wim_size
        info["install_wim_path"] = win_wim or ""
        info["recommended_scheme"] = "gpt"
        info["recommended_write_mode"] = "iso"

        four_gib = 4 * 1024 * 1024 * 1024 - 1
        if wim_size > four_gib:
            info["needs_uefi_ntfs"] = True
            info["recommended_fs"] = "ntfs"
            gb = wim_size / (1024 ** 3)
            info["notes"].append(
                f"{win_wim} mesure {gb:.2f} Go (> 4 Go) : NTFS + chargeur UEFI:NTFS automatiquement activé."
            )
        else:
            info["recommended_fs"] = "ntfs"
            info["notes"].append("Image Windows compatible UEFI/GPT avec personnalisation autounattend.xml.")

        fname = info["filename"].lower()
        if "win11" in fname or "windows_11" in fname or "windows 11" in fname:
            info["os_label"] = "Windows 11"
            if "25h2" in fname:
                info["os_label"] = "Windows 11 25H2"
            elif "24h2" in fname:
                info["os_label"] = "Windows 11 24H2"
        elif "win10" in fname or "windows_10" in fname:
            info["os_label"] = "Windows 10"
        else:
            info["os_label"] = "Windows Setup"
        return

    # Linux detection
    has_casper = any(p.startswith("casper/") for p in paths_lower)
    has_live = any(p.startswith("live/vmlinuz") or p.startswith("live/filesystem.squashfs") for p in paths_lower)
    has_arch = any(p.startswith("arch/boot/") for p in paths_lower)
    has_fedora = any(p.startswith("liveos/") for p in paths_lower)

    if has_casper or has_live or has_arch or has_fedora or any("isolinux" in p for p in paths_lower):
        info["os_family"] = "linux"
        info["recommended_scheme"] = "gpt"
        info["recommended_fs"] = "fat32"
        if has_casper:
            info["persistence_type"] = "casper-rw"
            info["os_label"] = "Linux (Ubuntu / Mint / Casper)"
            info["recommended_write_mode"] = "iso"
            info["notes"].append("Distribution type Ubuntu/Casper détectée : persistance `casper-rw` compatible.")
        elif has_live:
            info["persistence_type"] = "debian-persistence"
            info["os_label"] = "Linux (Debian / Kali Live)"
            info["recommended_write_mode"] = "iso" if info["has_uefi_boot"] else "dd"
            info["notes"].append("Distribution Debian/Kali Live détectée : persistance `persistence.conf` compatible.")
        elif has_arch:
            info["persistence_type"] = "none"
            info["os_label"] = "Arch Linux"
            info["recommended_write_mode"] = "dd"
            info["notes"].append("ISO Arch Linux détectée : mode DD ou Ventoy recommandé.")
        elif has_fedora:
            info["persistence_type"] = "none"
            info["os_label"] = "Fedora / Red Hat Live"
            info["recommended_write_mode"] = "dd"
            info["notes"].append("ISO Fedora détectée : mode DD ou Ventoy recommandé.")
        else:
            info["os_label"] = "Linux / Live ISO"
            info["recommended_write_mode"] = "dd" if info["is_hybrid_boot"] else "iso"


def compute_hashes(
    file_path: str,
    algorithms: Optional[List[str]] = None,
    expected_hash: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
) -> Dict[str, Any]:
    """Stream-compute cryptographic hashes (SHA-256 / SHA-1 / MD5) with optional constant-time match."""
    import time

    algos = [a.lower() for a in (algorithms or ["sha256"]) if a.lower() in ("sha256", "sha1", "md5", "sha512")]
    if not algos:
        algos = ["sha256"]

    real_path = os.path.realpath(file_path)
    total_bytes = os.path.getsize(real_path)
    hashers = {a: hashlib.new(a) for a in algos}

    chunk_size = 4 * 1024 * 1024
    read_bytes = 0
    t0 = time.monotonic()
    last_emit = t0

    with open(real_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            for h in hashers.values():
                h.update(chunk)
            read_bytes += len(chunk)
            now = time.monotonic()
            if progress_cb and (now - last_emit >= 0.15 or read_bytes == total_bytes):
                elapsed = max(now - t0, 0.001)
                progress_cb(read_bytes, total_bytes, read_bytes / elapsed)
                last_emit = now

    digests = {a: h.hexdigest() for a, h in hashers.items()}
    matched: Optional[bool] = None
    matched_algo: Optional[str] = None
    if expected_hash:
        clean_expected = re.sub(r"\s+", "", expected_hash).lower()
        matched = False
        for algo, digest in digests.items():
            if len(clean_expected) == len(digest) and hmac.compare_digest(clean_expected, digest):
                matched = True
                matched_algo = algo
                break

    return {
        "path": real_path,
        "size_bytes": total_bytes,
        "hashes": digests,
        "expected": expected_hash,
        "matched": matched,
        "matched_algorithm": matched_algo,
    }
