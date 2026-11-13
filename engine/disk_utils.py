#!/usr/bin/env python3
"""Disk inspection, safety validation, and partition layout planning for BootForge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Strictly allow whole-disk Linux block device nodes only (no partitions like sdb1 or nvme0n1p1)
WHOLE_DISK_RE = re.compile(r"^/dev/(sd[a-z]+|vd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")
SAFE_LABEL_FAT_RE = re.compile(r"[^A-Za-z0-9_-]")
SAFE_LABEL_NTFS_RE = re.compile(r"[^A-Za-z0-9 _-]")

CRITICAL_MOUNTPOINTS = {
    "/",
    "/boot",
    "/boot/efi",
    "/home",
    "/var",
    "/usr",
    "/etc",
    "/opt",
    "/srv",
    "/tmp",
    "/root",
    "/snap",
    "[SWAP]",
}


def is_critical_mountpoint(mp: str) -> bool:
    """Return True if a mountpoint hosts core OS / system directories or subtrees."""
    clean = (mp or "").strip()
    if not clean:
        return False
    if clean in CRITICAL_MOUNTPOINTS:
        return True
    for prefix in ("/boot/", "/var/", "/usr/", "/etc/", "/opt/", "/srv/", "/snap/"):
        if clean.startswith(prefix):
            return True
    return False


def _collect_nested_children(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Recursively flatten children across partitions, LUKS/crypt, LVM, and MD nodes."""
    found: List[Dict[str, Any]] = []
    for child in node.get("children") or []:
        if isinstance(child, dict):
            found.append(child)
            found.extend(_collect_nested_children(child))
    return found


class SafetyViolationError(ValueError):
    """Raised when an operation targets a system disk or violates input validation."""


@dataclass
class PartitionInfo:
    name: str
    path: str
    size_bytes: int
    fstype: str
    label: str
    uuid: str
    mountpoints: List[str]


@dataclass
class BlockDeviceInfo:
    name: str
    path: str
    size_bytes: int
    model: str
    vendor: str
    serial: str
    transport: str
    removable: bool
    read_only: bool
    hotplug: bool
    pttype: str
    usb_speed_mbps: Optional[int]
    vid_pid: Optional[str]
    is_system_disk: bool
    system_reasons: List[str]
    ventoy_detected: bool
    partitions: List[PartitionInfo]


def _flatten_mountpoints(node: Dict[str, Any]) -> List[str]:
    mounts: List[str] = []
    mp = node.get("mountpoint")
    if isinstance(mp, str) and mp:
        mounts.append(mp)
    mps = node.get("mountpoints")
    if isinstance(mps, list):
        for item in mps:
            if isinstance(item, str) and item and item not in mounts:
                mounts.append(item)
    return mounts


def _collect_sys_usb_metadata(dev_name: str) -> tuple[Optional[int], Optional[str]]:
    """Read USB link speed (Mbps) and VID:PID from /sys or udevadm without shell."""
    speed_mbps: Optional[int] = None
    vid_pid: Optional[str] = None

    sys_block = Path(f"/sys/block/{dev_name}")
    if sys_block.exists():
        try:
            resolved = sys_block.resolve()
            # Walk up sysfs parents looking for USB device attributes (idVendor, idProduct, speed)
            for parent in [resolved, *resolved.parents]:
                vendor_f = parent / "idVendor"
                product_f = parent / "idProduct"
                speed_f = parent / "speed"
                if vendor_f.exists() and product_f.exists():
                    vid = vendor_f.read_text(encoding="utf-8", errors="ignore").strip()
                    pid = product_f.read_text(encoding="utf-8", errors="ignore").strip()
                    if vid and pid:
                        vid_pid = f"{vid}:{pid}"
                if speed_f.exists() and speed_mbps is None:
                    raw_speed = speed_f.read_text(encoding="utf-8", errors="ignore").strip()
                    if raw_speed.isdigit():
                        speed_mbps = int(raw_speed)
                if vid_pid and speed_mbps is not None:
                    break
        except OSError:
            pass

    if vid_pid is None and shutil.which("udevadm"):
        try:
            proc = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name=/dev/{dev_name}"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode == 0:
                props: Dict[str, str] = {}
                for line in proc.stdout.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
                vid = props.get("ID_VENDOR_ID")
                pid = props.get("ID_MODEL_ID")
                if vid and pid:
                    vid_pid = f"{vid}:{pid}"
        except (OSError, subprocess.SubprocessError):
            pass

    return speed_mbps, vid_pid


def parse_lsblk_json(raw_json: str, collect_sysfs: bool = True) -> List[BlockDeviceInfo]:
    """Parse `lsblk -J -b -p` JSON output and evaluate system-disk protection flags."""
    data = json.loads(raw_json)
    devices: List[BlockDeviceInfo] = []

    for dev in data.get("blockdevices", []):
        dev_type = str(dev.get("type") or "")
        if dev_type != "disk":
            continue

        path = str(dev.get("path") or dev.get("name") or "")
        name = os.path.basename(str(dev.get("kname") or dev.get("name") or ""))
        if not path.startswith("/dev/"):
            path = f"/dev/{name}"

        # Ignore loop, zram, sr optical drives
        if name.startswith(("loop", "zram", "sr", "dm-", "md")):
            continue

        size_bytes = int(dev.get("size") or 0)
        if size_bytes <= 0:
            continue

        model = str(dev.get("model") or "").strip() or name
        vendor = str(dev.get("vendor") or "").strip()
        serial = str(dev.get("serial") or "").strip()
        transport = str(dev.get("tran") or "").strip().lower()
        removable = bool(dev.get("rm"))
        read_only = bool(dev.get("ro"))
        hotplug = bool(dev.get("hotplug"))
        pttype = str(dev.get("pttype") or "").strip().lower()

        system_reasons: List[str] = []
        partitions: List[PartitionInfo] = []

        # Check top-level mounts
        for mp in _flatten_mountpoints(dev):
            if is_critical_mountpoint(mp):
                system_reasons.append(f"Point de montage système actif ({mp})")

        ventoy_detected = False
        part_labels: List[str] = []

        direct_children = dev.get("children") or []
        nested_children = _collect_nested_children(dev)

        for child in direct_children:
            c_name = os.path.basename(str(child.get("kname") or child.get("name") or ""))
            c_path = str(child.get("path") or f"/dev/{c_name}")
            c_size = int(child.get("size") or 0)
            c_fstype = str(child.get("fstype") or "").strip()
            c_label = str(child.get("label") or "").strip()
            c_uuid = str(child.get("uuid") or "").strip()
            c_mounts = _flatten_mountpoints(child)

            part_labels.append(c_label.upper())
            for mp in c_mounts:
                if is_critical_mountpoint(mp):
                    system_reasons.append(f"Partition système active {c_name} ({mp})")

            partitions.append(
                PartitionInfo(
                    name=c_name,
                    path=c_path,
                    size_bytes=c_size,
                    fstype=c_fstype,
                    label=c_label,
                    uuid=c_uuid,
                    mountpoints=c_mounts,
                )
            )

        # Also inspect deeper descendants (LUKS dm-crypt, LVM LVs, MD arrays) for OS mountpoints
        direct_ids = {id(c) for c in direct_children}
        for desc in nested_children:
            if id(desc) in direct_ids:
                continue
            d_name = os.path.basename(str(desc.get("kname") or desc.get("name") or ""))
            for mp in _flatten_mountpoints(desc):
                if is_critical_mountpoint(mp):
                    system_reasons.append(f"Conteneur système imbriqué {d_name} ({mp})")

        if "VTOYEFI" in part_labels:
            ventoy_detected = True

        # Non-USB internal disks (NVMe, SATA, VirtIO without hotplug/removable) are marked protected
        if not removable and not hotplug and transport != "usb":
            system_reasons.append(f"Disque interne non-USB ({transport or 'local'})")

        is_system_disk = len(system_reasons) > 0
        usb_speed_mbps, vid_pid = _collect_sys_usb_metadata(name) if collect_sysfs else (None, None)

        devices.append(
            BlockDeviceInfo(
                name=name,
                path=path,
                size_bytes=size_bytes,
                model=model,
                vendor=vendor,
                serial=serial,
                transport=transport,
                removable=removable,
                read_only=read_only,
                hotplug=hotplug,
                pttype=pttype,
                usb_speed_mbps=usb_speed_mbps,
                vid_pid=vid_pid,
                is_system_disk=is_system_disk,
                system_reasons=system_reasons,
                ventoy_detected=ventoy_detected,
                partitions=partitions,
            )
        )

    return devices


def list_block_devices() -> List[Dict[str, Any]]:
    """Query live block devices on Linux (lsblk) or Windows (Get-Disk)."""
    if sys.platform == "win32":
        from engine.win_backend import list_windows_block_devices

        return list_windows_block_devices()

    proc = subprocess.run(
        [
            "lsblk",
            "-J",
            "-b",
            "-p",
            "-o",
            "NAME,KNAME,SIZE,MODEL,VENDOR,SERIAL,TRAN,RM,RO,HOTPLUG,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MOUNTPOINTS,PTTYPE",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return [asdict(d) for d in parse_lsblk_json(proc.stdout, collect_sysfs=True)]


def validate_target_device(
    device_path: str,
    devices: Optional[List[BlockDeviceInfo]] = None,
    allow_non_removable_usb: bool = True,
) -> BlockDeviceInfo:
    """Strictly validate that target is a safe whole removable/USB disk and NOT an OS disk."""
    if sys.platform == "win32" and not str(device_path or "").startswith("/dev/"):
        from engine.win_backend import validate_windows_target

        match, _disk_num = validate_windows_target(device_path, devices=devices)
        return match

    if not isinstance(device_path, str):
        raise SafetyViolationError(
            f"Chemin de périphérique invalide ou interdit (disque entier attendu) : {device_path!r}"
        )

    # Canonicalize existing device nodes & symlinks (prevents /dev/../dev/... tricks) while preserving mocked paths
    if os.path.lexists(device_path):
        resolved_path = os.path.realpath(device_path)
    else:
        resolved_path = os.path.normpath(device_path)
    if not WHOLE_DISK_RE.match(resolved_path):
        raise SafetyViolationError(
            f"Chemin de périphérique invalide ou interdit (disque entier attendu) : {device_path!r}"
        )

    if devices is None:
        proc = subprocess.run(
            [
                "lsblk",
                "-J",
                "-b",
                "-p",
                "-o",
                "NAME,KNAME,SIZE,MODEL,VENDOR,SERIAL,TRAN,RM,RO,HOTPLUG,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MOUNTPOINTS,PTTYPE",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        devices = parse_lsblk_json(proc.stdout, collect_sysfs=True)

    match = next((d for d in devices if d.path == resolved_path), None)
    if match is None:
        raise SafetyViolationError(f"Périphérique introuvable : {resolved_path}")

    if match.read_only:
        raise SafetyViolationError(f"Le périphérique {resolved_path} est protégé en écriture (lecture seule).")

    # Hard block any disk holding critical OS mountpoints (at any partition/LUKS/LVM depth)
    for part in match.partitions:
        for mp in part.mountpoints:
            if is_critical_mountpoint(mp):
                raise SafetyViolationError(
                    f"BLOCAGE SÉCURITÉ : {resolved_path} héberge une partition système ({mp})."
                )

    is_usb_like = match.removable or match.hotplug or (allow_non_removable_usb and match.transport == "usb")
    if not is_usb_like:
        raise SafetyViolationError(
            f"BLOCAGE SÉCURITÉ : {resolved_path} n'est pas identifié comme média USB amovible (tran={match.transport}, rm={match.removable})."
        )

    if match.is_system_disk:
        reason_txt = "; ".join(match.system_reasons) or "disque protégé"
        raise SafetyViolationError(
            f"BLOCAGE SÉCURITÉ : {resolved_path} est protégé comme disque système ({reason_txt})."
        )

    return match


def validate_paths_not_on_device(device: BlockDeviceInfo, *paths: Optional[str]) -> None:
    """Reject if any input/output file or directory resides on a mounted partition of target device."""
    mount_roots: List[Path] = []
    for part in device.partitions:
        for mp in part.mountpoints:
            clean = (mp or "").strip()
            if clean and clean != "[SWAP]":
                try:
                    mount_roots.append(Path(clean).resolve())
                except OSError:
                    continue

    if not mount_roots:
        return

    for raw_p in paths:
        if not raw_p or not str(raw_p).strip():
            continue
        try:
            candidate = Path(str(raw_p)).resolve()
        except OSError:
            continue
        for mroot in mount_roots:
            if candidate == mroot or candidate.is_relative_to(mroot):
                raise SafetyViolationError(
                    f"BLOCAGE SÉCURITÉ : le chemin {raw_p} se trouve sur le périphérique cible {device.path} ({mroot})."
                )


def sanitize_volume_label(label: str, fstype: str) -> str:
    """Sanitize filesystem volume label according to strict per-FS limits."""
    raw = (label or "BOOTFORGE").strip()
    fs = fstype.lower()
    if fs in ("fat32", "vfat", "fat16"):
        cleaned = SAFE_LABEL_FAT_RE.sub("_", raw).upper()[:11]
        return cleaned or "BOOTFORGE"
    if fs == "exfat":
        cleaned = SAFE_LABEL_NTFS_RE.sub("_", raw)[:15]
        return cleaned or "BootForge"
    if fs == "ext4":
        cleaned = SAFE_LABEL_FAT_RE.sub("_", raw)[:16]
        return cleaned or "persistence"
    # NTFS
    cleaned = SAFE_LABEL_NTFS_RE.sub("_", raw)[:32]
    return cleaned or "BootForge"


def plan_partition_layout(
    disk_size_bytes: int,
    mode: str,
    scheme: str = "gpt",
    filesystem: str = "ntfs",
    write_mode: str = "iso",
    persistence_gb: float = 0.0,
    ventoy_reserve_mb: int = 0,
    uefi_ntfs: bool = True,
) -> List[Dict[str, Any]]:
    """Compute a precise partition blueprint for both UI preview and parted/sgdisk execution."""
    mib = 1024 * 1024
    gib = 1024 * mib
    total_mib = max(disk_size_bytes // mib, 64)

    segments: List[Dict[str, Any]] = []

    if mode == "ventoy":
        reserved = max(0, min(ventoy_reserve_mb, total_mib // 2))
        vtoy_efi_mib = 32
        data_mib = max(total_mib - vtoy_efi_mib - reserved - 2, 16)
        segments.append(
            {
                "id": "ventoy-data",
                "label": "Ventoy (ISOs + Données)",
                "filesystem": filesystem if filesystem in ("exfat", "ntfs", "fat32") else "exfat",
                "role": "data",
                "size_mib": data_mib,
                "start_mib": 1,
                "end_mib": 1 + data_mib,
                "color": "cyan",
            }
        )
        segments.append(
            {
                "id": "ventoy-efi",
                "label": "VTOYEFI",
                "filesystem": "fat16",
                "role": "boot",
                "size_mib": vtoy_efi_mib,
                "start_mib": 1 + data_mib,
                "end_mib": 1 + data_mib + vtoy_efi_mib,
                "color": "amber",
            }
        )
        if reserved > 0:
            segments.append(
                {
                    "id": "ventoy-reserved",
                    "label": "Espace réservé",
                    "filesystem": "unallocated",
                    "role": "reserved",
                    "size_mib": reserved,
                    "start_mib": 1 + data_mib + vtoy_efi_mib,
                    "end_mib": total_mib,
                    "color": "slate",
                }
            )
        return segments

    # Direct Flash (Rufus mode)
    if write_mode == "dd":
        segments.append(
            {
                "id": "dd-raw",
                "label": "Image hybride brute (DD)",
                "filesystem": "iso9660/raw",
                "role": "raw",
                "size_mib": total_mib,
                "start_mib": 0,
                "end_mib": total_mib,
                "color": "amber",
            }
        )
        return segments

    persist_mib = int(max(0.0, persistence_gb) * 1024)
    # Keep at least 2 GiB for payload if persistence requested
    if persist_mib > 0:
        max_persist = max(0, total_mib - 2048)
        persist_mib = min(persist_mib, max_persist)

    fs_norm = filesystem.lower()
    needs_uefi_ntfs = fs_norm in ("ntfs", "exfat") and uefi_ntfs
    uefi_ntfs_mib = 2 if needs_uefi_ntfs else 0
    payload_mib = max(total_mib - persist_mib - uefi_ntfs_mib - 2, 32)

    cursor = 1
    segments.append(
        {
            "id": "main-payload",
            "label": f"Système ({scheme.upper()})",
            "filesystem": fs_norm,
            "role": "payload",
            "size_mib": payload_mib,
            "start_mib": cursor,
            "end_mib": cursor + payload_mib,
            "color": "amber" if fs_norm == "ntfs" else "emerald",
        }
    )
    cursor += payload_mib

    if persist_mib > 0:
        segments.append(
            {
                "id": "linux-persistence",
                "label": "Persistance Linux",
                "filesystem": "ext4",
                "role": "persistence",
                "size_mib": persist_mib,
                "start_mib": cursor,
                "end_mib": cursor + persist_mib,
                "color": "violet",
            }
        )
        cursor += persist_mib

    if needs_uefi_ntfs:
        segments.append(
            {
                "id": "uefi-ntfs",
                "label": "UEFI:NTFS",
                "filesystem": "fat16",
                "role": "boot",
                "size_mib": uefi_ntfs_mib,
                "start_mib": cursor,
                "end_mib": cursor + uefi_ntfs_mib,
                "color": "cyan",
            }
        )

    return segments


def check_system_dependencies() -> Dict[str, Any]:
    """Check presence of core and optional tools needed by BootForge on the current OS."""
    if sys.platform == "win32":
        from engine.win_backend import check_windows_dependencies

        return check_windows_dependencies()

    core_bins = {
        "lsblk": "Inventaire sécurisé des disques",
        "parted": "Création de tables GPT / MBR",
        "sgdisk": "Gestion fine des GUID GPT",
        "wipefs": "Nettoyage des signatures de partitions",
        "mkfs.vfat": "Formatage FAT32 / ESP",
        "mkfs.ntfs": "Formatage NTFS (Windows > 4 Go)",
        "mkfs.exfat": "Formatage exFAT (Ventoy)",
        "mkfs.ext4": "Formatage ext4 (Persistance Linux)",
        "7z": "Extraction et inspection d'ISO sans montage",
        "dd": "Écriture brute DD & sauvegarde .img",
        "pkexec": "Élévation de privilèges PolicyKit",
        "badblocks": "Test de surface & blocs défectueux",
    }
    optional_bins = {
        "wimlib-imagex": ("Inspection éditions WIM & découpe install.swm", "sudo apt install wimtools"),
        "qemu-system-x86_64": ("Test de boot live UEFI intégré", "sudo apt install qemu-system-x86 ovmf"),
        "mcopy": ("Manipulation FAT sans montage", "sudo apt install mtools"),
        "xorriso": ("Analyse avancée ISO9660", "sudo apt install xorriso"),
        "smartctl": ("Télémétrie santé S.M.A.R.T.", "sudo apt install smartmontools"),
    }

    core_status = []
    for binary, purpose in core_bins.items():
        path = shutil.which(binary)
        core_status.append({"name": binary, "installed": bool(path), "path": path or "", "purpose": purpose})

    optional_status = []
    for binary, (purpose, install_cmd) in optional_bins.items():
        path = shutil.which(binary)
        optional_status.append(
            {
                "name": binary,
                "installed": bool(path),
                "path": path or "",
                "purpose": purpose,
                "install_cmd": install_cmd,
            }
        )

    project_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    bundled_uefi_ntfs = project_root / "assets" / "uefi-ntfs" / "uefi-ntfs.img"
    cached_uefi_ntfs = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bootforge" / "uefi-ntfs" / "uefi-ntfs.img"
    uefi_ntfs_img = bundled_uefi_ntfs if bundled_uefi_ntfs.is_file() else cached_uefi_ntfs
    ventoy_cache_dir = (
        Path("/var/cache/bootforge/ventoy")
        if hasattr(os, "geteuid") and os.geteuid() == 0
        else Path("/var/cache/bootforge/ventoy")
        if Path("/var/cache/bootforge/ventoy").exists()
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bootforge" / "ventoy"
    )

    return {
        "core": core_status,
        "optional": optional_status,
        "core_ready": all(item["installed"] for item in core_status),
        "uefi_ntfs_cached": uefi_ntfs_img.exists() and uefi_ntfs_img.stat().st_size > 100000,
        "uefi_ntfs_path": str(uefi_ntfs_img),
        "ventoy_cache_dir": str(ventoy_cache_dir),
    }


if __name__ == "__main__":
    print(json.dumps({"devices": list_block_devices(), "dependencies": check_system_dependencies()}, indent=2))
