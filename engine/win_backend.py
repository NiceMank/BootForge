#!/usr/bin/env python3
"""Windows native USB/Disk inspection, diskpart formatting, raw Win32 I/O, DISM split, and Ventoy CLI runner."""

from __future__ import annotations

import ctypes
import gzip
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from engine.disk_utils import (
    BlockDeviceInfo,
    PartitionInfo,
    SafetyViolationError,
    plan_partition_layout,
    sanitize_volume_label,
    validate_paths_not_on_device,
)
from engine.iso_inspector import inspect_image
from engine.unattend_builder import build_autounattend_xml
from engine.ventoy_manager import (
    build_ventoy_json,
    build_ventoy_windows_cli_args,
    download_and_extract_ventoy_windows,
    find_cached_ventoy_windows_dir,
    normalize_ventoy_image_path,
)

WIN_WHOLE_DISK_RE = re.compile(r"^\\\\\.\\PhysicalDrive(\d+)$", re.IGNORECASE)

# Win32 CreateFile & DeviceIoControl constants
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_NO_BUFFERING = 0x20000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020
FSCTL_ALLOW_EXTENDED_DASD_IO = 0x00090083


def is_windows_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_powershell_json(ps_script: str, timeout: int = 15, extra_env: Optional[Dict[str, str]] = None) -> Any:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"PowerShell error ({proc.returncode})")
    raw = proc.stdout.strip()
    if not raw:
        return []
    return json.loads(raw)


def parse_windows_disks_json(raw_json: str, system_drive_letter: Optional[str] = None) -> List[BlockDeviceInfo]:
    """Parse normalized JSON emitted by PowerShell disk inventory into BlockDeviceInfo objects."""
    data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    sys_drv = (system_drive_letter or os.environ.get("SystemDrive") or "C:").rstrip(":").upper()
    devices: List[BlockDeviceInfo] = []

    for item in data:
        try:
            num = int(item.get("Number"))
        except (TypeError, ValueError):
            continue

        size_bytes = int(item.get("Size") or 0)
        if size_bytes <= 0:
            continue

        name = f"PhysicalDrive{num}"
        path = rf"\\.\PhysicalDrive{num}"
        model = str(item.get("FriendlyName") or item.get("Model") or name).strip()
        serial = str(item.get("SerialNumber") or "").strip()
        bus_type = str(item.get("BusType") or "").strip()
        transport = bus_type.lower() or "local"
        media_type = str(item.get("MediaType") or "").strip().lower()
        removable = (transport == "usb") or ("removable" in media_type)
        read_only = bool(item.get("IsReadOnly", False))
        is_system_flag = bool(item.get("IsSystem", False))
        is_boot_flag = bool(item.get("IsBoot", False))
        pttype = str(item.get("PartitionStyle") or "").strip().lower()

        raw_parts = item.get("Partitions") or []
        if isinstance(raw_parts, dict):
            raw_parts = [raw_parts]

        partitions: List[PartitionInfo] = []
        system_reasons: List[str] = []
        if num == 0:
            system_reasons.append("Disque principal système (Disque 0)")
        if is_system_flag or is_boot_flag:
            system_reasons.append("Disque Windows actif (Système/Démarrage)")
        if transport != "usb":
            system_reasons.append(f"Disque interne non-USB ({bus_type or 'local'})")

        ventoy_detected = False
        for idx, part in enumerate(raw_parts, start=1):
            pnum = int(part.get("PartitionNumber") or idx)
            dl = str(part.get("DriveLetter") or "").strip().rstrip(":").upper()
            label = str(part.get("Label") or "").strip()
            fstype = str(part.get("FileSystem") or "").strip()
            psize = int(part.get("Size") or 0)

            mps = [f"{dl}:\\"] if dl else []
            if dl and dl == sys_drv:
                system_reasons.append(f"Contient le lecteur système ({dl}:)")
            if label.upper() == "VTOYEFI":
                ventoy_detected = True

            partitions.append(
                PartitionInfo(
                    name=f"Disk{num}Part{pnum}",
                    path=f"{dl}:\\" if dl else f"{path}#Part{pnum}",
                    size_bytes=psize,
                    fstype=fstype,
                    label=label,
                    uuid="",
                    mountpoints=mps,
                )
            )

        devices.append(
            BlockDeviceInfo(
                name=name,
                path=path,
                size_bytes=size_bytes,
                model=model,
                vendor="",
                serial=serial,
                transport=transport,
                removable=removable,
                read_only=read_only,
                hotplug=removable,
                pttype=pttype,
                usb_speed_mbps=None,
                vid_pid=None,
                is_system_disk=len(system_reasons) > 0,
                system_reasons=system_reasons,
                ventoy_detected=ventoy_detected,
                partitions=partitions,
            )
        )

    return devices


def list_windows_block_devices() -> List[Dict[str, Any]]:
    ps_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$vols = @{}
foreach ($v in (Get-Volume)) {
    if ($v.DriveLetter) {
        $vols[[string]$v.DriveLetter] = @{
            Label = [string]$v.FileSystemLabel
            FileSystem = [string]$v.FileSystem
        }
    }
}
$disks = @(Get-Disk | Sort-Object Number | ForEach-Object {
    $d = $_
    $parts = @(Get-Partition -DiskNumber $d.Number | Sort-Object PartitionNumber | ForEach-Object {
        $p = $_
        $dl = if ($p.DriveLetter -and [char]$p.DriveLetter -ne [char]0) { [string]$p.DriveLetter } else { "" }
        $vinfo = if ($dl -and $vols.ContainsKey($dl)) { $vols[$dl] } else { $null }
        $lbl = if ($vinfo) { $vinfo.Label } elseif ($p.GptType -eq '{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}' -and $p.Size -lt 67108864) { "" } else { "" }
        $fs = if ($vinfo) { $vinfo.FileSystem } else { "" }
        try {
            $pv = $p | Get-Volume -ErrorAction SilentlyContinue
            if ($pv -and $pv.FileSystemLabel) { $lbl = [string]$pv.FileSystemLabel }
            if ($pv -and $pv.FileSystem) { $fs = [string]$pv.FileSystem }
        } catch {}
        [PSCustomObject]@{
            PartitionNumber = [int]$p.PartitionNumber
            Offset = [int64]$p.Offset
            Size = [int64]$p.Size
            DriveLetter = $dl
            Label = $lbl
            FileSystem = $fs
        }
    })
    [PSCustomObject]@{
        Number = [int]$d.Number
        FriendlyName = [string]$d.FriendlyName
        SerialNumber = [string]$d.SerialNumber
        Size = [int64]$d.Size
        BusType = [string]$d.BusType
        MediaType = [string]$d.MediaType
        IsReadOnly = [bool]$d.IsReadOnly
        IsSystem = [bool]$d.IsSystem
        IsBoot = [bool]$d.IsBoot
        PartitionStyle = [string]$d.PartitionStyle
        Partitions = $parts
    }
})
$disks | ConvertTo-Json -Depth 5 -Compress
"""
    raw = _run_powershell_json(ps_script, timeout=12)
    return [asdict(d) for d in parse_windows_disks_json(raw)]


def validate_windows_target(
    device_path: str,
    devices: Optional[List[BlockDeviceInfo]] = None,
) -> tuple[BlockDeviceInfo, int]:
    """Validate Windows target is strictly a whole USB PhysicalDrive and NEVER an OS disk."""
    m = WIN_WHOLE_DISK_RE.match(str(device_path or "").strip())
    if not m:
        raise SafetyViolationError(
            f"Chemin de périphérique Windows invalide (\\\\.\\PhysicalDriveN attendu) : {device_path!r}"
        )
    disk_num = int(m.group(1))
    if disk_num == 0:
        raise SafetyViolationError("BLOCAGE SÉCURITÉ : PhysicalDrive0 (disque principal) interdit.")

    if devices is None:
        raw_dicts = list_windows_block_devices()
        devices = [
            BlockDeviceInfo(
                **{
                    **d,
                    "partitions": [PartitionInfo(**p) for p in d.get("partitions", [])],
                }
            )
            for d in raw_dicts
        ]

    norm_target = rf"\\.\PhysicalDrive{disk_num}"
    match = next((d for d in devices if d.path.lower() == norm_target.lower()), None)
    if match is None:
        raise SafetyViolationError(f"Périphérique introuvable : {norm_target}")
    if match.read_only:
        raise SafetyViolationError(f"Le périphérique {norm_target} est protégé en écriture (lecture seule).")
    if match.is_system_disk or match.transport != "usb":
        reasons = "; ".join(match.system_reasons) or f"transport={match.transport}"
        raise SafetyViolationError(f"BLOCAGE SÉCURITÉ : {norm_target} protégé ({reasons}).")

    return match, disk_num


def check_windows_dependencies() -> Dict[str, Any]:
    """Inspect native Windows tools and optional utilities for PreflightDialog."""
    sevenzip = shutil.which("7z")
    if not sevenzip:
        for cand in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
            if os.path.isfile(cand):
                sevenzip = cand
                break

    qemu = shutil.which("qemu-system-x86_64") or (
        r"C:\Program Files\qemu\qemu-system-x86_64.exe"
        if os.path.isfile(r"C:\Program Files\qemu\qemu-system-x86_64.exe")
        else None
    )

    core_specs = [
        ("powershell", "Inventaire disques & montage ISO natif", shutil.which("powershell")),
        ("diskpart", "Partitionnement GPT / MBR & formatage", shutil.which("diskpart")),
        ("dism", "Découpe WIM (> 4 Go) pour FAT32", shutil.which("dism")),
    ]
    core_status = [
        {"name": n, "installed": bool(p), "path": p or "", "purpose": desc}
        for n, desc, p in core_specs
    ]
    optional_status = [
        {
            "name": "7z.exe",
            "installed": bool(sevenzip),
            "path": sevenzip or "",
            "purpose": "Lecture rapide d'images ISO sans montage",
            "install_cmd": "winget install 7zip.7zip",
        },
        {
            "name": "qemu-system-x86_64.exe",
            "installed": bool(qemu),
            "path": qemu or "",
            "purpose": "Test de démarrage virtuel UEFI / BIOS",
            "install_cmd": "winget install SoftwareFreedomConservancy.QEMU",
        },
    ]

    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    bundled_uefi_ntfs = resource_root / "assets" / "uefi-ntfs" / "uefi-ntfs.img"
    local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    cached_uefi = Path(local_app) / "BootForge" / "uefi-ntfs" / "uefi-ntfs.img"
    uefi_img = bundled_uefi_ntfs if bundled_uefi_ntfs.is_file() else cached_uefi

    return {
        "core": core_status,
        "optional": optional_status,
        "core_ready": all(c["installed"] for c in core_status),
        "uefi_ntfs_cached": uefi_img.is_file() and uefi_img.stat().st_size > 100000,
        "uefi_ntfs_path": str(uefi_img),
        "ventoy_cache_dir": str(Path(local_app) / "BootForge" / "ventoy"),
    }


def _find_7z_windows() -> Optional[str]:
    found = shutil.which("7z")
    if found:
        return found
    for cand in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.isfile(cand):
            return cand
    return None


def inspect_iso_windows_mounted(iso_path: str) -> List[Dict[str, str]]:
    """Read-only Mount-DiskImage inspection fallback on Windows when 7-Zip is not installed."""
    ps = r"""
$img = Mount-DiskImage -LiteralPath $env:BF_ISO_PATH -NoDriveLetter:$false -PassThru -ErrorAction Stop
try {
    Start-Sleep -Milliseconds 350
    $vol = $img | Get-Volume | Select-Object -First 1
    if ($vol -and $vol.DriveLetter) {
        $root = "$($vol.DriveLetter):\"
        Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue | Select-Object -First 5000 | ForEach-Object {
            $rel = $_.FullName.Substring($root.Length) -replace '\\','/'
            [PSCustomObject]@{ Path = $rel; Size = [string]$_.Length }
        } | ConvertTo-Json -Compress
    } else {
        "[]"
    }
} finally {
    Dismount-DiskImage -LiteralPath $env:BF_ISO_PATH -ErrorAction SilentlyContinue | Out-Null
}
"""
    data = _run_powershell_json(ps, timeout=25, extra_env={"BF_ISO_PATH": iso_path})
    if isinstance(data, dict):
        return [data]
    return list(data or [])


def _mount_iso_get_drive(iso_path: str) -> str:
    ps = r"""
$img = Mount-DiskImage -LiteralPath $env:BF_ISO_PATH -PassThru -ErrorAction Stop
Start-Sleep -Milliseconds 400
$vol = $img | Get-Volume | Where-Object { $_.DriveLetter } | Select-Object -First 1
if (-not $vol) { throw "Aucun lecteur monté pour l'image ISO." }
[PSCustomObject]@{ DriveLetter = [string]$vol.DriveLetter } | ConvertTo-Json -Compress
"""
    res = _run_powershell_json(ps, timeout=20, extra_env={"BF_ISO_PATH": iso_path})
    dl = str((res or {}).get("DriveLetter") or "").strip().rstrip(":")
    if not dl:
        raise RuntimeError("Impossible d'obtenir la lettre du lecteur ISO monté.")
    return f"{dl}:\\"


def _dismount_iso(iso_path: str) -> None:
    env = os.environ.copy()
    env["BF_ISO_PATH"] = iso_path
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Dismount-DiskImage -LiteralPath $env:BF_ISO_PATH -ErrorAction SilentlyContinue | Out-Null",
        ],
        env=env,
        capture_output=True,
        check=False,
    )


def build_diskpart_flash_script(
    disk_num: int,
    scheme: str,
    filesystem: str,
    volume_label: str,
    disk_size_bytes: int,
    uefi_ntfs_boot: bool = False,
) -> List[str]:
    """Generate a clean, deterministic diskpart command sequence for Windows Direct Flash."""
    fs = filesystem.lower()
    if fs not in ("ntfs", "fat32", "exfat"):
        fs = "ntfs"
    safe_lbl = sanitize_volume_label(volume_label, fs).replace('"', "")
    dp_fs = "fat32" if fs == "fat32" else ("exfat" if fs == "exfat" else "ntfs")

    cmds = [
        f"select disk {disk_num}",
        "clean",
        "convert gpt" if scheme.lower() == "gpt" else "convert mbr",
    ]

    if uefi_ntfs_boot and fs in ("ntfs", "exfat"):
        total_mb = max(disk_size_bytes // (1024 * 1024), 64)
        main_mb = max(total_mb - 8, 32)
        cmds.extend(
            [
                f"create partition primary size={main_mb}",
                f'format fs={dp_fs} quick label="{safe_lbl}"',
                "assign",
                "create partition primary",
                "format fs=fat quick label=\"UEFI_NTFS\"",
            ]
        )
    else:
        cmds.extend(
            [
                "create partition primary",
                f'format fs={dp_fs} quick label="{safe_lbl}"',
                "assign",
            ]
        )
    cmds.append("exit")
    return cmds


def _run_diskpart(commands: List[str], emit_log: Callable[[str, str], None]) -> str:
    script = "\n".join(commands) + "\n"
    emit_log("cmd", f"diskpart << {'; '.join(commands[:5])} ...")
    proc = subprocess.run(
        ["diskpart"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.stdout:
        for line in proc.stdout.strip().splitlines()[-8:]:
            emit_log("stdout", line)
    if proc.returncode != 0:
        raise RuntimeError(f"diskpart a échoué ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _get_disk_partitions_win(disk_num: int) -> List[Dict[str, Any]]:
    ps = f"""
@(Get-Partition -DiskNumber {disk_num} -ErrorAction SilentlyContinue | Sort-Object PartitionNumber | ForEach-Object {{
    $p = $_
    $dl = if ($p.DriveLetter -and [char]$p.DriveLetter -ne [char]0) {{ [string]$p.DriveLetter }} else {{ "" }}
    $lbl = ""
    try {{ $v = $p | Get-Volume -ErrorAction SilentlyContinue; if ($v) {{ $lbl = [string]$v.FileSystemLabel }} }} catch {{}}
    [PSCustomObject]@{{
        PartitionNumber = [int]$p.PartitionNumber
        Offset = [int64]$p.Offset
        Size = [int64]$p.Size
        DriveLetter = $dl
        Label = $lbl
    }}
}}) | ConvertTo-Json -Compress
"""
    res = _run_powershell_json(ps, timeout=10)
    if isinstance(res, dict):
        return [res]
    return list(res or [])


def _lock_and_dismount_disk_volumes(disk_num: int) -> List[int]:
    """Acquire FSCTL_LOCK_VOLUME + FSCTL_DISMOUNT_VOLUME on all mounted drive letters of a USB disk."""
    kernel32 = ctypes.windll.kernel32
    handles: List[int] = []
    for p in _get_disk_partitions_win(disk_num):
        dl = str(p.get("DriveLetter") or "").strip().rstrip(":")
        if not dl:
            continue
        vol_path = rf"\\.\{dl}:"
        h = kernel32.CreateFileW(
            vol_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if h == INVALID_HANDLE_VALUE or not h:
            continue
        bytes_ret = ctypes.c_ulong(0)
        locked = False
        for _ in range(3):
            if kernel32.DeviceIoControl(h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None):
                locked = True
                break
            time.sleep(0.15)
        if not locked:
            err = ctypes.GetLastError()
            kernel32.CloseHandle(h)
            _close_handles(handles)
            raise RuntimeError(
                f"Impossible de verrouiller le volume {vol_path} (Win32={err}). Fermez les fenêtres de l'Explorateur."
            )
        if not kernel32.DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None):
            err = ctypes.GetLastError()
            kernel32.CloseHandle(h)
            _close_handles(handles)
            raise RuntimeError(f"Impossible de démonter le volume {vol_path} (Win32={err}).")
        handles.append(h)
    return handles


def _close_handles(handles: List[int]) -> None:
    kernel32 = ctypes.windll.kernel32
    for h in handles:
        try:
            bytes_ret = ctypes.c_ulong(0)
            kernel32.DeviceIoControl(h, FSCTL_UNLOCK_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None)
            kernel32.CloseHandle(h)
        except Exception:
            pass


def _open_windows_image_stream(image_path: str):
    """Open raw, .gz, or .xz disk image cleanly and expose underlying compressed reader if applicable."""
    lower_name = image_path.lower()
    raw_fin = open(image_path, "rb")
    if lower_name.endswith(".gz"):
        return raw_fin, gzip.GzipFile(fileobj=raw_fin, mode="rb"), True
    if lower_name.endswith(".xz"):
        return raw_fin, lzma.open(raw_fin, mode="rb"), True
    return raw_fin, raw_fin, False


def _write_raw_windows_disk(
    disk_num: int,
    offset_bytes: int,
    stream_opener: Callable[[], Any],
    total_hint: int,
    emit_progress: Callable[..., None],
    stage: str,
    label: str,
    base_pct: float,
    span_pct: float,
    *,
    max_bytes: Optional[int] = None,
    raw_reader: Optional[Any] = None,
) -> None:
    """Write raw sector-aligned stream directly to \\\\.\\PhysicalDriveN after volume locking."""
    kernel32 = ctypes.windll.kernel32
    vol_handles = _lock_and_dismount_disk_volumes(disk_num)
    disk_path = rf"\\.\PhysicalDrive{disk_num}"
    h_disk = kernel32.CreateFileW(
        disk_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_WRITE_THROUGH | FILE_FLAG_NO_BUFFERING,
        None,
    )
    if h_disk == INVALID_HANDLE_VALUE or not h_disk:
        err = ctypes.GetLastError()
        _close_handles(vol_handles)
        raise RuntimeError(f"Impossible d'ouvrir {disk_path} en écriture brute (Win32={err}).")

    try:
        bytes_ret = ctypes.c_ulong(0)
        kernel32.DeviceIoControl(h_disk, FSCTL_ALLOW_EXTENDED_DASD_IO, None, 0, None, 0, ctypes.byref(bytes_ret), None)

        if offset_bytes > 0:
            new_pos = ctypes.c_int64(0)
            if not kernel32.SetFilePointerEx(h_disk, ctypes.c_int64(offset_bytes), ctypes.byref(new_pos), 0):
                raise RuntimeError(f"Échec positionnement secteur à {offset_bytes} (Win32={ctypes.GetLastError()}).")

        chunk_size = 4 * 1024 * 1024
        written = 0
        t0 = time.monotonic()
        last_emit = t0
        written_out = ctypes.c_ulong(0)

        with stream_opener() as fin:
            while True:
                buf = fin.read(chunk_size)
                if not buf:
                    break
                # Direct physical disk NO_BUFFERING writes must be 512-byte sector aligned
                rem = len(buf) % 512
                if rem:
                    buf = buf + b"\x00" * (512 - rem)
                if max_bytes is not None and offset_bytes + written + len(buf) > max_bytes:
                    raise RuntimeError(
                        f"L'image dépasse la capacité du périphérique cible ({max_bytes} octets)."
                    )
                c_buf = (ctypes.c_char * len(buf)).from_buffer_copy(buf)
                ok = kernel32.WriteFile(h_disk, c_buf, len(buf), ctypes.byref(written_out), None)
                if not ok:
                    raise RuntimeError(f"Erreur d'écriture secteur sur {disk_path} (Win32={ctypes.GetLastError()}).")
                written += len(buf)
                now = time.monotonic()
                if now - last_emit >= 0.25:
                    elapsed = max(now - t0, 0.001)
                    speed = written / elapsed
                    if raw_reader is not None:
                        ratio = min(1.0, max(0.0, raw_reader.tell() / max(total_hint, 1)))
                        pct = base_pct + ratio * span_pct
                    else:
                        pct = base_pct + min(span_pct, (written / max(total_hint, 1)) * span_pct)
                    emit_progress(
                        pct,
                        stage,
                        f"{label} : {written / (1024**2):.0f} Mo ({speed / (1024**2):.1f} Mo/s)",
                        speed_bps=speed,
                    )
                    last_emit = now

        kernel32.FlushFileBuffers(h_disk)
    finally:
        kernel32.CloseHandle(h_disk)
        _close_handles(vol_handles)


def execute_win_flash_direct(
    job: Dict[str, Any],
    dry_run: bool,
    emit: Callable[..., None],
    emit_progress: Callable[..., None],
    emit_log: Callable[[str, str], None],
    ensure_uefi_ntfs_image: Callable[[], Path],
    copy_tree_with_progress: Callable[..., None],
) -> None:
    if not is_windows_admin() and not dry_run:
        raise RuntimeError("Droits administrateur Windows requis (UAC).")

    device_path = str(job["device"])
    image_path = os.path.realpath(str(job["image_path"]))
    scheme = str(job.get("scheme") or "gpt").lower()
    filesystem = str(job.get("filesystem") or "ntfs").lower()
    write_mode = str(job.get("write_mode") or "iso").lower()
    volume_label = sanitize_volume_label(str(job.get("volume_label") or "BOOTFORGE"), filesystem)
    win_tweaks = job.get("windows_tweaks") or {}
    driver_dir = str(job.get("driver_dir") or "").strip()

    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Fichier image introuvable : {image_path}")

    dev_info, disk_num = validate_windows_target(device_path)
    validate_paths_not_on_device(dev_info, image_path, driver_dir)
    img_info = inspect_image(image_path)

    emit_progress(2, "validate", f"Validation de {dev_info.path} ({dev_info.model})...")
    if dry_run:
        layout = plan_partition_layout(
            dev_info.size_bytes,
            "direct",
            scheme=scheme,
            filesystem=filesystem,
            write_mode=write_mode,
            persistence_gb=0.0,
            uefi_ntfs=(filesystem in ("ntfs", "exfat")),
        )
        emit("dry_run_plan", mode="flash_direct", device=dev_info.path, image=image_path, layout=layout)
        emit_progress(100, "done", "Simulation (dry-run) validée sans écriture.")
        return

    if write_mode == "dd":
        emit_progress(6, "wipe", f"Nettoyage de {dev_info.path} via diskpart...")
        _run_diskpart([f"select disk {disk_num}", "clean", "exit"], emit_log)

        total_size = os.path.getsize(image_path)
        emit_progress(10, "dd", "Écriture bloc par bloc (mode DD) en cours...")
        raw_fin, stream_obj, is_compressed = _open_windows_image_stream(image_path)
        try:
            _write_raw_windows_disk(
                disk_num,
                0,
                lambda: stream_obj,
                total_size,
                emit_progress,
                "dd",
                "Écriture DD",
                10.0,
                85.0,
                max_bytes=dev_info.size_bytes,
                raw_reader=raw_fin if is_compressed else None,
            )
        finally:
            try:
                if is_compressed:
                    stream_obj.close()
            finally:
                raw_fin.close()
        _run_diskpart(["rescan", "exit"], emit_log)
        emit_progress(100, "done", "Image écrite en mode DD avec succès !")
        return

    needs_uefi_ntfs = filesystem in ("ntfs", "exfat")
    emit_progress(8, "partition", "Préparation des partitions et formatage via diskpart...")
    dp_cmds = build_diskpart_flash_script(
        disk_num,
        scheme,
        filesystem,
        volume_label,
        dev_info.size_bytes,
        uefi_ntfs_boot=needs_uefi_ntfs,
    )
    _run_diskpart(dp_cmds, emit_log)
    time.sleep(0.8)

    parts = _get_disk_partitions_win(disk_num)
    if not parts or not parts[0].get("DriveLetter"):
        raise RuntimeError("Lettre de lecteur introuvable après formatage USB.")

    usb_root = Path(f"{parts[0]['DriveLetter']}:\\")

    if needs_uefi_ntfs and len(parts) >= 2:
        uefi_img = ensure_uefi_ntfs_image()
        boot_part = parts[1]
        boot_offset = int(boot_part["Offset"])
        if boot_part.get("DriveLetter"):
            _run_diskpart([f"select disk {disk_num}", "select partition 2", "remove", "exit"], emit_log)
        emit_progress(18, "bootloader", "Écriture du chargeur officiel Rufus UEFI:NTFS...")
        _write_raw_windows_disk(
            disk_num,
            boot_offset,
            lambda: open(uefi_img, "rb"),
            uefi_img.stat().st_size,
            emit_progress,
            "bootloader",
            "UEFI:NTFS",
            18.0,
            4.0,
        )

    iso_drive: Optional[str] = None
    extracted_tmp: Optional[str] = None
    try:
        emit_progress(24, "mount", "Montage de l'image source...")
        try:
            iso_drive = _mount_iso_get_drive(image_path)
            iso_root = Path(iso_drive)
        except Exception:
            sevenzip = _find_7z_windows()
            if not sevenzip:
                raise
            extracted_tmp = tempfile.mkdtemp(prefix="bf_winiso_")
            subprocess.run([sevenzip, "x", image_path, f"-o{extracted_tmp}", "-y"], check=True)
            iso_root = Path(extracted_tmp)

        wim_candidates = (iso_root / "sources" / "install.wim", iso_root / "x64" / "sources" / "install.wim")
        install_wim = next((p for p in wim_candidates if p.is_file()), wim_candidates[0])
        wim_rel_dir = install_wim.parent.relative_to(iso_root)
        split_wim = False
        if (
            filesystem == "fat32"
            and install_wim.is_file()
            and install_wim.stat().st_size > (4 * 1024**3 - 1)
        ):
            if shutil.which("dism"):
                split_wim = True
                emit_log("info", f"Découpe de {wim_rel_dir / 'install.wim'} (> 4 Go) en segments install.swm via DISM...")
            else:
                raise RuntimeError("install.wim dépasse 4 Go : choisissez NTFS + UEFI:NTFS.")

        if split_wim:
            all_files: List[tuple[Path, Path, int]] = []
            for root, _, files in os.walk(iso_root):
                rel = Path(root).relative_to(iso_root)
                (usb_root / rel).mkdir(parents=True, exist_ok=True)
                for fn in files:
                    sf = Path(root) / fn
                    if sf == install_wim:
                        continue
                    all_files.append((sf, usb_root / rel / fn, sf.stat().st_size))
            total_b = sum(x[2] for x in all_files)
            done_b = 0
            for sf, df, sz in all_files:
                shutil.copy2(sf, df)
                done_b += sz
                emit_progress(25 + (done_b / max(total_b, 1)) * 35, "copying", f"Copie {sf.name}...")

            emit_progress(62, "wimsplit", "Découpe de install.wim vers install.swm (DISM 3800 Mo)...")
            (usb_root / wim_rel_dir).mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [
                    "dism",
                    "/Split-Image",
                    f"/ImageFile:{install_wim}",
                    f"/SWMFile:{usb_root / wim_rel_dir / 'install.swm'}",
                    "/FileSize:3800",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"Échec DISM /Split-Image : {proc.stdout}\n{proc.stderr}")
        else:
            copy_tree_with_progress(iso_root, usb_root, 25.0, 65.0)

        if img_info["os_family"] == "windows" and win_tweaks.get("enabled", True):
            emit_progress(92, "customize", "Génération et injection de autounattend.xml...")
            (usb_root / "autounattend.xml").write_text(build_autounattend_xml(win_tweaks), encoding="utf-8")

        if driver_dir and os.path.isdir(os.path.realpath(driver_dir)):
            emit_progress(94, "drivers", "Injection du dossier de pilotes ($WinPEDriver$)...")
            dest_drv = usb_root / "$WinPEDriver$"
            src_root = Path(os.path.realpath(driver_dir))
            for root, dirs, files in os.walk(src_root, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
                rel = Path(root).relative_to(src_root)
                target_sub = dest_drv / rel
                target_sub.mkdir(parents=True, exist_ok=True)
                for fn in files:
                    sf = Path(root) / fn
                    if not sf.is_symlink() and sf.is_file():
                        shutil.copy2(sf, target_sub / fn)

        emit_progress(100, "done", "Clé USB bootable créée avec succès !")
    finally:
        if iso_drive:
            _dismount_iso(image_path)
        if extracted_tmp:
            shutil.rmtree(extracted_tmp, ignore_errors=True)


def execute_win_ventoy(
    job: Dict[str, Any],
    dry_run: bool,
    emit: Callable[..., None],
    emit_progress: Callable[..., None],
    emit_log: Callable[[str, str], None],
    copy_with_progress: Callable[..., None],
) -> None:
    action = str(job["action"])
    device_path = str(job["device"])
    dev_info, disk_num = validate_windows_target(device_path)

    scheme = str(job.get("scheme") or "gpt").lower()
    secure_boot = bool(job.get("secure_boot", True))
    reserve_mb = int(job.get("reserve_mb") or 0)
    ventoy_cfg = job.get("ventoy_config") or {}
    add_isos = job.get("add_isos") or []
    validate_paths_not_on_device(dev_info, *[str(p) for p in add_isos])
    remove_isos = job.get("remove_isos") or []
    new_persistences = job.get("create_persistence") or []
    unattend_profiles = job.get("unattend_profiles") or []

    if dry_run:
        emit(
            "dry_run_plan",
            mode=action,
            device=dev_info.path,
            ventoy_json=build_ventoy_json(ventoy_cfg),
            add_isos=add_isos,
            new_persistences=new_persistences,
        )
        emit_progress(100, "done", "Simulation Ventoy (dry-run) validée.")
        return

    if not is_windows_admin():
        raise RuntimeError("Droits administrateur Windows requis (UAC).")

    if new_persistences:
        raise RuntimeError(
            "La création de fichiers .dat ext4 sous Windows requiert Ventoy Live / Linux ou l'outil CreatePersistentImg.sh."
        )

    if action in ("ventoy_install", "ventoy_update"):
        emit_progress(4, "ventoy-fetch", "Vérification / téléchargement de Ventoy pour Windows...")
        ventoy_dir = find_cached_ventoy_windows_dir() or download_and_extract_ventoy_windows(
            progress_cb=lambda p, m: emit_progress(4 + p * 0.14, "ventoy-fetch", m)
        )
        cli_args = build_ventoy_windows_cli_args(action, disk_num, scheme, secure_boot, reserve_mb)
        cmd = [str(ventoy_dir / "Ventoy2Disk.exe"), *cli_args]
        emit_log("cmd", f"$ {' '.join(cmd)}")
        emit_progress(20, "ventoy-disk", f"Exécution de Ventoy2Disk.exe sur Disque {disk_num}...")

        proc = subprocess.run(
            cmd,
            cwd=str(ventoy_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Erreur Ventoy2Disk.exe ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
        time.sleep(1.5)

    parts = _get_disk_partitions_win(disk_num)
    data_part = next((p for p in parts if str(p.get("Label") or "").upper() != "VTOYEFI"), parts[0] if parts else None)
    if not data_part:
        raise RuntimeError("Partition de données Ventoy introuvable.")
    dl = str(data_part.get("DriveLetter") or "").strip().rstrip(":")
    if not dl:
        pnum = int(data_part["PartitionNumber"])
        _run_diskpart([f"select disk {disk_num}", f"select partition {pnum}", "assign", "exit"], emit_log)
        time.sleep(0.8)
        parts = _get_disk_partitions_win(disk_num)
        data_part = next((p for p in parts if int(p["PartitionNumber"]) == pnum), {})
        dl = str(data_part.get("DriveLetter") or "").strip().rstrip(":")

    if not dl:
        raise RuntimeError("Impossible d'obtenir la lettre du volume Ventoy.")

    vroot = Path(f"{dl}:\\")
    vdir = vroot / "ventoy"
    vdir.mkdir(parents=True, exist_ok=True)

    vroot_resolved = vroot.resolve()
    for rel in remove_isos:
        norm = normalize_ventoy_image_path(str(rel)).lstrip("/")
        target_file = (vroot_resolved / norm).resolve()
        if target_file != vroot_resolved and target_file.is_relative_to(vroot_resolved) and target_file.is_file():
            emit_log("info", f"Suppression de {norm}...")
            target_file.unlink()

    if unattend_profiles:
        sdir = vdir / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        for prof in unattend_profiles:
            fname = os.path.basename(str(prof.get("filename") or "win_unattend.xml"))
            if not fname.endswith(".xml"):
                fname += ".xml"
            (sdir / fname).write_text(build_autounattend_xml(prof.get("options") or {}), encoding="utf-8")

    emit_progress(45, "config", "Écriture de /ventoy/ventoy.json...")
    vjson_obj = build_ventoy_json(ventoy_cfg)
    tmp_vj = vdir / ".ventoy.json.tmp"
    tmp_vj.write_text(json.dumps(vjson_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp_vj, vdir / "ventoy.json")

    if add_isos:
        total_iso_bytes = sum(os.path.getsize(os.path.realpath(p)) for p in add_isos if os.path.isfile(p))
        span = 50.0
        cursor_pct = 47.0
        for raw_p in add_isos:
            src_p = Path(os.path.realpath(str(raw_p)))
            if not src_p.is_file():
                continue
            item_span = (src_p.stat().st_size / max(total_iso_bytes, 1)) * span
            copy_with_progress(src_p, vroot / src_p.name, cursor_pct, item_span, f"ISO {src_p.name}")
            cursor_pct += item_span

    emit_progress(100, "done", "Configuration Multi-Boot Ventoy terminée avec succès !")


def execute_win_toolbox(
    job: Dict[str, Any],
    dry_run: bool,
    emit: Callable[..., None],
    emit_progress: Callable[..., None],
    emit_log: Callable[[str, str], None],
) -> None:
    action = str(job["action"])
    device_path = str(job["device"])
    dev_info, disk_num = validate_windows_target(device_path)

    if dry_run:
        emit("dry_run_plan", mode=action, device=dev_info.path)
        emit_progress(100, "done", "Simulation outil validée.")
        return

    if not is_windows_admin():
        raise RuntimeError("Droits administrateur Windows requis (UAC).")

    if action == "usb_wipe_reset":
        fs = str(job.get("filesystem") or "exfat").lower()
        scheme = str(job.get("scheme") or "gpt").lower()
        label = sanitize_volume_label(str(job.get("volume_label") or "USB_CLEAN"), fs)
        emit_progress(20, "wipe", f"Réinitialisation de {dev_info.path} via diskpart...")
        cmds = build_diskpart_flash_script(disk_num, scheme, fs, label, dev_info.size_bytes, uefi_ntfs_boot=False)
        _run_diskpart(cmds, emit_log)
        emit_progress(100, "done", "Clé USB réinitialisée à 100 % de sa capacité d'usine.")

    elif action == "usb_benchmark":
        emit_progress(10, "benchmark", f"Mesure du débit de lecture sur {dev_info.path}...")
        sample_bytes = min(256 * 1024 * 1024, dev_info.size_bytes)
        read_bytes = 0
        t0 = time.monotonic()
        with open(dev_info.path, "rb") as fin:
            while read_bytes < sample_bytes:
                buf = fin.read(4 * 1024 * 1024)
                if not buf:
                    break
                read_bytes += len(buf)
                emit_progress(10 + (read_bytes / sample_bytes) * 85, "benchmark", "Lecture séquentielle 256 Mo...")
        elapsed = max(time.monotonic() - t0, 0.001)
        mbps = (read_bytes / (1024 * 1024)) / elapsed
        emit("benchmark_result", read_mbps=round(mbps, 2), sampled_mb=round(read_bytes / (1024 * 1024), 1))
        emit_progress(100, "done", f"Vitesse de lecture séquentielle mesurée : {mbps:.1f} Mo/s")

    elif action == "usb_badblocks":
        destructive = bool(job.get("destructive", False))
        emit_progress(5, "badblocks", f"Contrôle de surface ({'écriture/lecture' if destructive else 'lecture seule'})...")
        total = (min(dev_info.size_bytes, 64 * 1024**3) // 512) * 512
        chunk = 4 * 1024 * 1024
        bad_count = 0

        if destructive:
            _run_diskpart([f"select disk {disk_num}", "clean", "exit"], emit_log)
            emit_log("warn", "Test destructif écriture+relecture (0xAA55) en cours sur la clé...")
            pattern_block = b"\xAA\x55" * (chunk // 2)
            # Write pass
            def _pattern_gen():
                class _PatStream:
                    def __init__(self) -> None:
                        self.rem = total
                    def read(self, n: int) -> bytes:
                        if self.rem <= 0:
                            return b""
                        take = min(n, self.rem)
                        self.rem -= take
                        return pattern_block[:take]
                    def __enter__(self):
                        return self
                    def __exit__(self, *a):
                        return False
                return _PatStream()

            _write_raw_windows_disk(
                disk_num,
                0,
                _pattern_gen,
                total,
                emit_progress,
                "badblocks",
                "Écriture motif test",
                5.0,
                45.0,
            )

        done = 0
        with open(dev_info.path, "rb") as fin:
            while done < total:
                to_read = min(chunk, total - done)
                try:
                    fin.seek(done)
                    buf = fin.read(to_read)
                    if not buf:
                        break
                    if destructive and buf != (b"\xAA\x55" * (len(buf) // 2))[:len(buf)]:
                        bad_count += 1
                except OSError:
                    bad_count += 1
                done += to_read
                if (done // chunk) % 8 == 0:
                    base_p = 50.0 if destructive else 5.0
                    span_p = 48.0 if destructive else 93.0
                    emit_progress(base_p + (done / max(total, 1)) * span_p, "badblocks", f"Analyse : {done // (1024**2)} / {total // (1024**2)} Mo")
        emit("badblocks_result", bad_blocks=bad_count)
        emit_progress(100, "done", f"Analyse terminée : {bad_count} secteur(s) défectueux détecté(s).")

    elif action == "usb_backup_img":
        output_path = os.path.realpath(str(job["output_path"]))
        validate_paths_not_on_device(dev_info, output_path)
        compress = bool(job.get("compress_gz", False))
        if not output_path.endswith((".img", ".img.gz")):
            output_path += ".img.gz" if compress else ".img"
        total = dev_info.size_bytes
        copied = 0
        t0 = time.monotonic()
        last_emit = t0
        chunk_size = 4 * 1024 * 1024
        opener = (lambda p: gzip.open(p, "wb", compresslevel=4)) if compress else (lambda p: open(p, "wb"))
        with open(dev_info.path, "rb") as fin, opener(output_path) as fout:
            while True:
                buf = fin.read(chunk_size)
                if not buf:
                    break
                fout.write(buf)
                copied += len(buf)
                now = time.monotonic()
                if now - last_emit >= 0.3:
                    elapsed = max(now - t0, 0.001)
                    speed = copied / elapsed
                    pct = (copied / max(total, 1)) * 100.0
                    emit_progress(
                        pct,
                        "backup",
                        f"Sauvegarde : {copied / (1024**3):.2f} / {total / (1024**3):.2f} Go ({speed / (1024**2):.1f} Mo/s)",
                        speed_bps=speed,
                    )
                    last_emit = now
        emit_progress(100, "done", f"Sauvegarde image créée : {output_path}")

    elif action == "usb_restore_img":
        image_path = os.path.realpath(str(job["image_path"]))
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Fichier image introuvable : {image_path}")
        validate_paths_not_on_device(dev_info, image_path)
        emit_progress(5, "wipe", f"Nettoyage de {dev_info.path} via diskpart...")
        _run_diskpart([f"select disk {disk_num}", "clean", "exit"], emit_log)
        total_size = os.path.getsize(image_path)
        raw_fin, stream_obj, is_compressed = _open_windows_image_stream(image_path)
        try:
            _write_raw_windows_disk(
                disk_num,
                0,
                lambda: stream_obj,
                total_size,
                emit_progress,
                "restore",
                "Restauration image",
                10.0,
                85.0,
                max_bytes=dev_info.size_bytes,
                raw_reader=raw_fin if is_compressed else None,
            )
        finally:
            try:
                if is_compressed:
                    stream_obj.close()
            finally:
                raw_fin.close()
        _run_diskpart(["rescan", "exit"], emit_log)
        emit_progress(100, "done", "Image disque restaurée avec succès sur la clé USB !")
    else:
        raise ValueError(f"Action outil inconnue : {action}")
