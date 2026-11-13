#!/usr/bin/env python3
"""BootForge Core & Privileged System Worker.

Can run in unprivileged inspection mode (`devices`, `deps`, `inspect-iso`, `hash-file`, `ventoy-state`)
or in privileged execution mode via `pkexec python3 bootforge_engine.py --job <job.json>`.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure sibling imports work when invoked directly as script via pkexec or frozen binary
ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", ENGINE_DIR.parent))
if str(ENGINE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.disk_utils import (
    SafetyViolationError,
    check_system_dependencies,
    list_block_devices,
    plan_partition_layout,
    sanitize_volume_label,
    validate_paths_not_on_device,
    validate_target_device,
)
from engine.iso_inspector import compute_hashes, inspect_image
from engine.net_security import GITHUB_ALLOWED_HOSTS, download_https_atomic
from engine.unattend_builder import build_autounattend_xml
from engine.ventoy_manager import (
    build_ventoy_json,
    create_persistence_dat_image,
    download_and_extract_ventoy,
    find_cached_ventoy_dir,
    normalize_ventoy_image_path,
    read_ventoy_partition_state,
)


_EVENT_LOG_FP = None


def set_event_log_path(log_path: Optional[str]) -> None:
    global _EVENT_LOG_FP
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        _EVENT_LOG_FP = open(log_path, "a", encoding="utf-8")


def emit(event_type: str, **payload: Any) -> None:
    """Emit structured JSONL events to stdout (and optional event log file) for real-time telemetry."""
    msg = {"type": event_type, "ts": time.time(), **payload}
    line = json.dumps(msg, ensure_ascii=False)
    print(line, flush=True)
    if _EVENT_LOG_FP is not None:
        _EVENT_LOG_FP.write(line + "\n")
        _EVENT_LOG_FP.flush()


def emit_progress(percent: float, stage: str, message: str, speed_bps: Optional[float] = None) -> None:
    emit(
        "progress",
        percent=round(max(0.0, min(100.0, percent)), 1),
        stage=stage,
        message=message,
        speed_bps=speed_bps,
    )


def emit_log(level: str, message: str) -> None:
    emit("log", level=level, message=message)


def run_cmd(cmd: List[str], check: bool = True, stdin_data: Optional[str] = None) -> subprocess.CompletedProcess:
    emit_log("cmd", f"$ {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout:
        for line in proc.stdout.strip().splitlines()[-8:]:
            emit_log("stdout", line)
    if proc.stderr:
        for line in proc.stderr.strip().splitlines()[-8:]:
            emit_log("stderr", line)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Échec de commande ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc


def unmount_device_partitions(device_path: str) -> None:
    """Safely unmount all mounted partitions of target USB disk before repartitioning."""
    devices = list_block_devices()
    target = next((d for d in devices if d["path"] == device_path), None)
    if not target:
        return
    for part in target.get("partitions", []):
        for mp in part.get("mountpoints", []):
            if mp and mp != "[SWAP]":
                emit_log("info", f"Démontage de {part['path']} ({mp})...")
                run_cmd(["umount", "-f", part["path"]], check=False)
                time.sleep(0.3)
    # Wait udev settle
    if shutil.which("udevadm"):
        run_cmd(["udevadm", "settle"], check=False)


def resolve_partition_node(device_path: str, part_num: int) -> str:
    """Return /dev/sdb1 for /dev/sdb or /dev/nvme0n1p1 for /dev/nvme0n1."""
    base = os.path.basename(device_path)
    if base[-1].isdigit():
        return f"{device_path}p{part_num}"
    return f"{device_path}{part_num}"


def wait_for_partition(part_path: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if os.path.exists(part_path):
            return
        time.sleep(0.25)
    raise RuntimeError(f"Partition introuvable après partitionnement : {part_path}")


UEFI_NTFS_URL = "https://raw.githubusercontent.com/pbatard/rufus/v4.6/res/uefi/uefi-ntfs.img"
UEFI_NTFS_SHA256 = "72683fa1250eeea772d3399277b434d4e55ba8dd0dc926e52d817e701fc2eb9e"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest().lower()


def _safe_umount_cleanup(mnt_dir: str) -> None:
    """Unmount cleanly and remove mount directory only if unmount succeeded (avoid rm -rf on mounted FS)."""
    um = run_cmd(["umount", mnt_dir], check=False)
    if um.returncode == 0:
        try:
            os.rmdir(mnt_dir)
        except OSError:
            pass


def _copy_dir_no_symlinks(src_dir: str, dst_dir: Path) -> None:
    """Recursively copy driver directory while skipping symlinks to avoid host file leakage."""
    src_root = Path(os.path.realpath(src_dir))
    for root, dirs, files in os.walk(src_root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        rel = Path(root).relative_to(src_root)
        target_sub = dst_dir / rel
        target_sub.mkdir(parents=True, exist_ok=True)
        for fn in files:
            sf = Path(root) / fn
            if sf.is_symlink() or not sf.is_file():
                continue
            shutil.copy2(sf, target_sub / fn)


def _write_raw_stream_to_device(
    image_path: str,
    device_path: str,
    dev_size_bytes: int,
    stage: str = "dd",
    action_label: str = "Écriture DD",
) -> None:
    """Stream raw, .gz, or .xz disk image to block device with accurate progress & fsync."""
    lower_name = image_path.lower()
    comp_size = max(os.path.getsize(image_path), 1)
    t0 = time.monotonic()
    last_emit = t0
    written = 0
    chunk_size = 4 * 1024 * 1024

    with open(image_path, "rb") as raw_fin:
        if lower_name.endswith(".gz"):
            stream_ctx = gzip.GzipFile(fileobj=raw_fin, mode="rb")
            compressed = True
        elif lower_name.endswith(".xz"):
            stream_ctx = lzma.open(raw_fin, mode="rb")
            compressed = True
        else:
            stream_ctx = raw_fin
            compressed = False

        try:
            with open(device_path, "wb") as fout:
                while True:
                    buf = stream_ctx.read(chunk_size)
                    if not buf:
                        break
                    if written + len(buf) > dev_size_bytes:
                        raise RuntimeError(
                            f"L'image dépasse la capacité du périphérique cible ({dev_size_bytes} octets)."
                        )
                    fout.write(buf)
                    written += len(buf)
                    now = time.monotonic()
                    if now - last_emit >= 0.3:
                        elapsed = max(now - t0, 0.001)
                        speed = written / elapsed
                        if compressed:
                            ratio = raw_fin.tell() / comp_size
                            pct = 10 + min(85.0, max(0.0, ratio) * 85.0)
                        else:
                            pct = 10 + min(85.0, (written / comp_size) * 85.0)
                        emit_progress(
                            pct,
                            stage,
                            f"{action_label} : {written / (1024**2):.0f} Mo ({speed / (1024**2):.1f} Mo/s)",
                            speed_bps=speed,
                        )
                        last_emit = now
                fout.flush()
                os.fsync(fout.fileno())
        finally:
            if compressed:
                stream_ctx.close()


def ensure_uefi_ntfs_image() -> Path:
    """Locate bundled UEFI:NTFS FAT image or download with mandatory SHA-256 verification."""
    asset_path = PROJECT_ROOT / "assets" / "uefi-ntfs" / "uefi-ntfs.img"
    if asset_path.is_file() and asset_path.stat().st_size > 100000:
        if _file_sha256(asset_path) == UEFI_NTFS_SHA256:
            return asset_path
        emit_log("warn", "Empreinte de assets/uefi-ntfs/uefi-ntfs.img invalide, récupération distante...")

    if sys.platform == "win32":
        cache_base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "BootForge"
    elif hasattr(os, "geteuid") and os.geteuid() == 0:
        cache_base = Path("/var/cache/bootforge")
    else:
        cache_base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "bootforge"
    cached_asset = cache_base / "uefi-ntfs" / "uefi-ntfs.img"
    if cached_asset.is_file() and cached_asset.stat().st_size > 100000:
        if _file_sha256(cached_asset) == UEFI_NTFS_SHA256:
            return cached_asset
        cached_asset.unlink(missing_ok=True)

    emit_log("info", "Téléchargement vérifié de uefi-ntfs.img depuis le dépôt officiel Rufus...")
    return download_https_atomic(
        UEFI_NTFS_URL,
        cached_asset,
        allowed_hosts=GITHUB_ALLOWED_HOSTS,
        expected_sha256=UEFI_NTFS_SHA256,
        timeout=30,
    )


def copy_with_progress(src: Path, dst: Path, base_pct: float, span_pct: float, label: str) -> None:
    """Copy a large file (e.g. ISO or .img) with real-time throughput & progress events."""
    total = src.stat().st_size
    copied = 0
    chunk_size = 4 * 1024 * 1024
    t0 = time.monotonic()
    last_emit = t0

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            buf = fin.read(chunk_size)
            if not buf:
                break
            fout.write(buf)
            copied += len(buf)
            now = time.monotonic()
            if now - last_emit >= 0.25 or copied == total:
                elapsed = max(now - t0, 0.001)
                speed = copied / elapsed
                pct = base_pct + (copied / max(total, 1)) * span_pct
                mb_done = copied / (1024 * 1024)
                mb_tot = total / (1024 * 1024)
                emit_progress(
                    pct,
                    "copying",
                    f"{label} : {mb_done:.0f} / {mb_tot:.0f} Mo ({speed / (1024*1024):.1f} Mo/s)",
                    speed_bps=speed,
                )
                last_emit = now
        fout.flush()
        os.fsync(fout.fileno())


def copy_tree_with_progress(src_dir: Path, dst_dir: Path, base_pct: float, span_pct: float) -> None:
    """Copy extracted ISO file tree cleanly with byte-accurate progress telemetry."""
    all_files: List[tuple[Path, Path, int]] = []
    total_bytes = 0
    for root, dirs, files in os.walk(src_dir):
        rel_root = Path(root).relative_to(src_dir)
        (dst_dir / rel_root).mkdir(parents=True, exist_ok=True)
        for fname in files:
            sfile = Path(root) / fname
            dfile = dst_dir / rel_root / fname
            fsize = sfile.stat().st_size
            all_files.append((sfile, dfile, fsize))
            total_bytes += fsize

    copied_bytes = 0
    t0 = time.monotonic()
    last_emit = t0
    chunk_size = 2 * 1024 * 1024

    for sfile, dfile, fsize in all_files:
        with open(sfile, "rb") as fin, open(dfile, "wb") as fout:
            while True:
                buf = fin.read(chunk_size)
                if not buf:
                    break
                fout.write(buf)
                copied_bytes += len(buf)
                now = time.monotonic()
                if now - last_emit >= 0.25:
                    elapsed = max(now - t0, 0.001)
                    speed = copied_bytes / elapsed
                    pct = base_pct + (copied_bytes / max(total_bytes, 1)) * span_pct
                    emit_progress(
                        pct,
                        "copying",
                        f"Copie : {sfile.name} ({copied_bytes / (1024**3):.2f} / {total_bytes / (1024**3):.2f} Go)",
                        speed_bps=speed,
                    )
                    last_emit = now
            fout.flush()
    emit_progress(base_pct + span_pct, "sync", "Synchronisation des tampons disques...")


def execute_flash_direct(job: Dict[str, Any], dry_run: bool = False) -> None:
    """Execute Rufus-equivalent Direct Flash (ISO extraction + UEFI:NTFS/FAT32 + Tweaks OR DD Raw)."""
    device_path = str(job["device"])
    image_path = os.path.realpath(str(job["image_path"]))
    scheme = str(job.get("scheme") or "gpt").lower()
    filesystem = str(job.get("filesystem") or "ntfs").lower()
    write_mode = str(job.get("write_mode") or "iso").lower()
    volume_label = sanitize_volume_label(str(job.get("volume_label") or "BOOTFORGE"), filesystem)
    quick_format = bool(job.get("quick_format", True))
    persistence_gb = float(job.get("persistence_gb") or 0.0)
    persistence_type = str(job.get("persistence_type") or "casper-rw")
    win_tweaks = job.get("windows_tweaks") or {}
    driver_dir = str(job.get("driver_dir") or "").strip()

    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Fichier image introuvable : {image_path}")

    dev_info = validate_target_device(device_path)
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
            persistence_gb=persistence_gb,
            uefi_ntfs=(filesystem in ("ntfs", "exfat")),
        )
        emit("dry_run_plan", mode="flash_direct", device=device_path, image=image_path, layout=layout)
        emit_progress(100, "done", "Simulation (dry-run) validée sans écriture.")
        return

    unmount_device_partitions(device_path)

    # Mode DD Raw (hybride Linux / .img / .img.gz / .img.xz)
    if write_mode == "dd":
        emit_progress(6, "wipe", f"Effacement des signatures sur {device_path}...")
        run_cmd(["wipefs", "-a", device_path])
        emit_progress(10, "dd", "Écriture bloc par bloc (mode DD) en cours...")

        _write_raw_stream_to_device(image_path, device_path, dev_info.size_bytes, stage="dd", action_label="Écriture DD")

        run_cmd(["partprobe", device_path], check=False)
        emit_progress(100, "done", "Image écrite en mode DD avec succès !")
        return

    # Mode ISO Extract (Rufus-exact dual-partition UEFI:NTFS / FAT32 / Linux Persistence)
    layout = plan_partition_layout(
        dev_info.size_bytes,
        "direct",
        scheme=scheme,
        filesystem=filesystem,
        write_mode="iso",
        persistence_gb=persistence_gb,
        uefi_ntfs=(filesystem in ("ntfs", "exfat")),
    )

    emit_progress(6, "wipe", f"Nettoyage de la table de partitions sur {device_path}...")
    run_cmd(["wipefs", "-a", device_path])
    run_cmd(["parted", "-s", device_path, "mklabel", "gpt" if scheme == "gpt" else "msdos"])

    emit_progress(10, "partition", "Création des partitions...")
    part_map: Dict[str, str] = {}
    for idx, seg in enumerate(layout, start=1):
        start_str = f"{seg['start_mib']}MiB"
        end_str = f"{seg['end_mib']}MiB" if idx < len(layout) else "100%"
        fs_hint = seg["filesystem"]
        if fs_hint == "fat16":
            parted_fs = "fat16"
        elif fs_hint == "fat32":
            parted_fs = "fat32"
        elif fs_hint == "ext4":
            parted_fs = "ext4"
        else:
            parted_fs = "ntfs"

        if scheme == "gpt":
            run_cmd(["parted", "-s", device_path, "mkpart", seg["id"], parted_fs, start_str, end_str])
        else:
            run_cmd(["parted", "-s", device_path, "mkpart", "primary", parted_fs, start_str, end_str])

        if seg["role"] == "boot" and scheme == "gpt":
            run_cmd(["parted", "-s", device_path, "set", str(idx), "msftdata", "on"], check=False)
        if idx == 1 and scheme == "mbr":
            run_cmd(["parted", "-s", device_path, "set", "1", "boot", "on"], check=False)

        part_node = resolve_partition_node(device_path, idx)
        part_map[seg["id"]] = part_node

    run_cmd(["partprobe", device_path], check=False)
    if shutil.which("udevadm"):
        run_cmd(["udevadm", "settle"], check=False)

    for seg_id, pnode in part_map.items():
        wait_for_partition(pnode)

    # Format partitions
    emit_progress(15, "format", "Formatage des partitions...")
    main_part = part_map["main-payload"]
    if filesystem == "ntfs":
        cmd = ["mkfs.ntfs", "-L", volume_label]
        if quick_format:
            cmd.append("-f")
        cmd.append(main_part)
        run_cmd(cmd)
    elif filesystem == "exfat":
        run_cmd(["mkfs.exfat", "-L", volume_label, main_part])
    else:
        run_cmd(["mkfs.vfat", "-F", "32", "-n", volume_label, main_part])

    if "linux-persistence" in part_map:
        p_part = part_map["linux-persistence"]
        p_label = "casper-rw" if persistence_type == "casper-rw" else "persistence"
        emit_progress(18, "format", f"Formatage persistance Linux ({p_label})...")
        run_cmd(["mkfs.ext4", "-F", "-q", "-L", p_label, p_part])
        if p_label == "persistence":
            tmp_pmnt = tempfile.mkdtemp(prefix="bf_pmnt_")
            try:
                run_cmd(["mount", p_part, tmp_pmnt])
                (Path(tmp_pmnt) / "persistence.conf").write_text("/ union\n", encoding="utf-8")
                os.sync()
            finally:
                _safe_umount_cleanup(tmp_pmnt)

    if "uefi-ntfs" in part_map:
        uefi_img = ensure_uefi_ntfs_image()
        boot_part = part_map["uefi-ntfs"]
        emit_progress(20, "bootloader", "Écriture du chargeur officiel Rufus UEFI:NTFS...")
        with open(uefi_img, "rb") as fin, open(boot_part, "wb") as fout:
            shutil.copyfileobj(fin, fout)
            fout.flush()
            os.fsync(fout.fileno())

    # Extract ISO to temporary dir or direct mount ISO -> copy to USB
    tmp_iso_mnt = tempfile.mkdtemp(prefix="bf_iso_")
    tmp_usb_mnt = tempfile.mkdtemp(prefix="bf_usb_")
    try:
        emit_progress(24, "mount", "Montage de l'image source et de la clé USB...")
        run_cmd(["mount", "-o", "loop,ro", image_path, tmp_iso_mnt])
        if filesystem == "ntfs":
            run_cmd(["mount", "-t", "ntfs-3g", main_part, tmp_usb_mnt])
        else:
            run_cmd(["mount", main_part, tmp_usb_mnt])

        iso_root = Path(tmp_iso_mnt)
        usb_root = Path(tmp_usb_mnt)

        # Handle FAT32 automatic WIM splitting if install.wim > 4 GiB and wimlib-imagex is installed
        wim_candidates = (iso_root / "sources" / "install.wim", iso_root / "x64" / "sources" / "install.wim")
        install_wim = next((p for p in wim_candidates if p.is_file()), wim_candidates[0])
        wim_rel_dir = install_wim.parent.relative_to(iso_root)
        split_wim = False
        if (
            filesystem == "fat32"
            and install_wim.is_file()
            and install_wim.stat().st_size > (4 * 1024**3 - 1)
        ):
            if shutil.which("wimlib-imagex"):
                split_wim = True
                emit_log("info", f"Découpe de {wim_rel_dir / 'install.wim'} (>4 Go) en segments install.swm pour FAT32...")
            else:
                raise RuntimeError(
                    f"{wim_rel_dir / 'install.wim'} dépasse 4 Go : choisissez NTFS (UEFI:NTFS) ou installez `wimtools`."
                )

        if split_wim:
            # Copy everything except install.wim, then wimlib-imagex split
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
            emit_progress(62, "wimsplit", "Découpe de install.wim vers install.swm (3.8 Go max)...")
            (usb_root / wim_rel_dir).mkdir(parents=True, exist_ok=True)
            run_cmd(["wimlib-imagex", "split", str(install_wim), str(usb_root / wim_rel_dir / "install.swm"), "3800"])
        else:
            copy_tree_with_progress(iso_root, usb_root, 25.0, 65.0)

        # Inject Windows autounattend.xml if Windows image & tweaks enabled
        if img_info["os_family"] == "windows" and win_tweaks.get("enabled", True):
            emit_progress(92, "customize", "Génération et injection de autounattend.xml (Rufus Tweaks)...")
            xml_content = build_autounattend_xml(win_tweaks)
            (usb_root / "autounattend.xml").write_text(xml_content, encoding="utf-8")

        # Inject Windows/Linux driver folder if requested (skipping symlinks)
        if driver_dir and os.path.isdir(os.path.realpath(driver_dir)):
            emit_progress(94, "drivers", "Injection du dossier de pilotes ($WinPEDriver$)...")
            dest_drv = usb_root / "$WinPEDriver$"
            _copy_dir_no_symlinks(driver_dir, dest_drv)

        # Enable Linux persistence boot kernel flags if persistence partition created
        if "linux-persistence" in part_map and img_info["os_family"] == "linux":
            emit_log("info", "Partition de persistance créée. Utilisez l'option 'persistent' / 'persistence' au démarrage.")

        emit_progress(97, "sync", "Finalisation et synchronisation matérielle (fsync)...")
        os.sync()
    finally:
        _safe_umount_cleanup(tmp_iso_mnt)
        _safe_umount_cleanup(tmp_usb_mnt)

    emit_progress(100, "done", "Clé USB bootable créée avec succès !")


def execute_ventoy_operation(job: Dict[str, Any], dry_run: bool = False) -> None:
    """Install/Update Ventoy OR apply multi-OS configuration, persistence .dat, and ISOs."""
    action = str(job["action"])  # ventoy_install | ventoy_update | ventoy_apply
    device_path = str(job["device"])
    dev_info = validate_target_device(device_path)
    add_isos = job.get("add_isos") or []
    validate_paths_not_on_device(dev_info, *[str(p) for p in add_isos])

    scheme = str(job.get("scheme") or "gpt").lower()
    secure_boot = bool(job.get("secure_boot", True))
    reserve_mb = int(job.get("reserve_mb") or 0)
    ventoy_label = sanitize_volume_label(str(job.get("volume_label") or "Ventoy"), "exfat")
    ventoy_cfg = job.get("ventoy_config") or {}
    remove_isos = job.get("remove_isos") or []
    new_persistences = job.get("create_persistence") or []
    unattend_profiles = job.get("unattend_profiles") or []

    if dry_run:
        emit(
            "dry_run_plan",
            mode=action,
            device=device_path,
            ventoy_json=build_ventoy_json(ventoy_cfg),
            add_isos=add_isos,
            new_persistences=new_persistences,
        )
        emit_progress(100, "done", "Simulation Ventoy (dry-run) validée.")
        return

    if action in ("ventoy_install", "ventoy_update"):
        emit_progress(4, "ventoy-fetch", "Vérification / téléchargement de Ventoy...")
        ventoy_dir = find_cached_ventoy_dir() or download_and_extract_ventoy(
            progress_cb=lambda p, m: emit_progress(4 + p * 0.12, "ventoy-fetch", m)
        )
        unmount_device_partitions(device_path)
        emit_progress(18, "ventoy-disk", f"Exécution de Ventoy2Disk ({action}) sur {device_path}...")

        cmd = ["bash", str(ventoy_dir / "Ventoy2Disk.sh")]
        if action == "ventoy_update":
            cmd.append("-u")
        else:
            cmd.append("-I")
            if scheme == "gpt":
                cmd.append("-g")
            cmd.append("-s" if secure_boot else "-S")
            cmd.extend(["-L", ventoy_label])
            if reserve_mb > 0:
                cmd.extend(["-r", str(reserve_mb)])
        cmd.append(device_path)

        stdin_answers = "y\n" if action == "ventoy_update" else "y\ny\n"
        proc = subprocess.run(
            cmd,
            cwd=str(ventoy_dir),
            input=stdin_answers,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout:
            for line in proc.stdout.strip().splitlines()[-10:]:
                emit_log("stdout", line)
        if proc.returncode != 0:
            raise RuntimeError(f"Erreur Ventoy2Disk : {proc.stderr.strip() or proc.stdout.strip()}")

        if shutil.which("udevadm"):
            run_cmd(["udevadm", "settle"], check=False)

    # Mount Ventoy data partition (partition 1) to write configs, persistence, scripts, ISOs
    data_part = resolve_partition_node(device_path, 1)
    wait_for_partition(data_part)
    tmp_mnt = tempfile.mkdtemp(prefix="bf_vtoy_")
    already_mounted = False

    # Check if already mounted by desktop auto-mounter
    devices_now = list_block_devices()
    for d in devices_now:
        if d["path"] == device_path:
            for p in d.get("partitions", []):
                if p["path"] == data_part and p.get("mountpoints"):
                    tmp_mnt = p["mountpoints"][0]
                    already_mounted = True
                    break

    try:
        if not already_mounted:
            run_cmd(["mount", data_part, tmp_mnt])

        vroot = Path(tmp_mnt)
        vdir = vroot / "ventoy"
        vdir.mkdir(parents=True, exist_ok=True)

        # 1. Remove requested ISOs
        vroot_resolved = vroot.resolve()
        for rel in remove_isos:
            norm = normalize_ventoy_image_path(str(rel)).lstrip("/")
            target_file = (vroot_resolved / norm).resolve()
            if target_file != vroot_resolved and target_file.is_relative_to(vroot_resolved) and target_file.is_file():
                emit_log("info", f"Suppression de {norm}...")
                target_file.unlink()

        # 2. Generate any per-ISO autounattend.xml profiles
        if unattend_profiles:
            sdir = vdir / "scripts"
            sdir.mkdir(parents=True, exist_ok=True)
            for prof in unattend_profiles:
                fname = os.path.basename(str(prof.get("filename") or "win_unattend.xml"))
                if not fname.endswith(".xml"):
                    fname += ".xml"
                xml_str = build_autounattend_xml(prof.get("options") or {})
                (sdir / fname).write_text(xml_str, encoding="utf-8")
                emit_log("info", f"Profil XML créé : /ventoy/scripts/{fname}")

        # 3. Create any Linux persistence .dat images
        if new_persistences:
            pdir = vdir / "persistence"
            pdir.mkdir(parents=True, exist_ok=True)
            for idx, pitem in enumerate(new_persistences, start=1):
                fname = os.path.basename(str(pitem.get("filename") or f"persist_{idx}.dat"))
                if not fname.endswith(".dat"):
                    fname += ".dat"
                size_mb = int(pitem.get("size_mb") or 2048)
                plabel = str(pitem.get("label") or "casper-rw")
                with_conf = bool(pitem.get("with_persistence_conf", plabel == "persistence"))
                emit_progress(
                    30 + idx * 5,
                    "persistence",
                    f"Création du conteneur persistant {fname} ({size_mb} Mo, label={plabel})...",
                )
                create_persistence_dat_image(str(pdir / fname), size_mb, label=plabel, with_persistence_conf=with_conf)

        # 4. Write ventoy.json atomically
        emit_progress(45, "config", "Écriture de /ventoy/ventoy.json...")
        vjson_obj = build_ventoy_json(ventoy_cfg)
        vjson_txt = json.dumps(vjson_obj, indent=2, ensure_ascii=False) + "\n"
        tmp_vj = vdir / ".ventoy.json.tmp"
        tmp_vj.write_text(vjson_txt, encoding="utf-8")
        os.replace(tmp_vj, vdir / "ventoy.json")

        # 5. Copy new ISOs onto the Ventoy partition
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

        emit_progress(98, "sync", "Synchronisation des écritures sur la clé USB...")
        os.sync()
    finally:
        if not already_mounted:
            _safe_umount_cleanup(tmp_mnt)

    emit_progress(100, "done", "Configuration Multi-Boot Ventoy terminée avec succès !")


def execute_toolbox_operation(job: Dict[str, Any], dry_run: bool = False) -> None:
    """Execute USB utilities: wipe_reset, badblocks, backup_img, benchmark."""
    action = str(job["action"])
    device_path = str(job["device"])
    dev_info = validate_target_device(device_path)

    if dry_run:
        emit("dry_run_plan", mode=action, device=device_path)
        emit_progress(100, "done", "Simulation outil validée.")
        return

    if action == "usb_wipe_reset":
        fs = str(job.get("filesystem") or "exfat").lower()
        scheme = str(job.get("scheme") or "gpt").lower()
        label = sanitize_volume_label(str(job.get("volume_label") or "USB_DRIVE"), fs)
        unmount_device_partitions(device_path)
        emit_progress(15, "wipe", f"Effacement des tables et signatures sur {device_path}...")
        run_cmd(["wipefs", "-a", device_path])
        run_cmd(["parted", "-s", device_path, "mklabel", "gpt" if scheme == "gpt" else "msdos"])
        parted_fs = "fat32" if fs == "fat32" else "ntfs"
        run_cmd(["parted", "-s", device_path, "mkpart", "primary", parted_fs, "1MiB", "100%"])
        run_cmd(["partprobe", device_path], check=False)
        part1 = resolve_partition_node(device_path, 1)
        wait_for_partition(part1)
        emit_progress(60, "format", f"Formatage propre en {fs.upper()}...")
        if fs == "fat32":
            run_cmd(["mkfs.vfat", "-F", "32", "-n", label, part1])
        elif fs == "ntfs":
            run_cmd(["mkfs.ntfs", "-f", "-L", label, part1])
        else:
            run_cmd(["mkfs.exfat", "-L", label, part1])
        os.sync()
        emit_progress(100, "done", "Clé USB réinitialisée à 100 % de sa capacité d'usine.")

    elif action == "usb_badblocks":
        destructive = bool(job.get("destructive", False))
        unmount_device_partitions(device_path)
        emit_progress(5, "badblocks", f"Analyse de surface {'écriture/lecture' if destructive else 'lecture seule'} sur {device_path}...")
        cmd = ["badblocks", "-s", "-v", "-b", "4096"]
        if destructive:
            cmd.append("-w")
        cmd.append(device_path)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        bad_count = 0
        assert proc.stdout is not None
        for line in proc.stdout:
            clean = line.strip()
            if clean:
                emit_log("badblocks", clean)
                m = re.search(r"(\d+\.\d+)%\s+done", clean)
                if m:
                    emit_progress(float(m.group(1)), "badblocks", clean)
                if "bad blocks found" in clean.lower():
                    m2 = re.search(r"(\d+)\s+bad blocks", clean.lower())
                    if m2:
                        bad_count = int(m2.group(1))
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"badblocks a quitté avec le code {rc}")
        emit("badblocks_result", bad_blocks=bad_count)
        emit_progress(100, "done", f"Analyse terminée : {bad_count} secteur(s) défectueux détecté(s).")

    elif action == "usb_backup_img":
        output_path = os.path.realpath(str(job["output_path"]))
        validate_paths_not_on_device(dev_info, output_path)
        compress = bool(job.get("compress_gz", False))
        if not output_path.endswith((".img", ".img.gz")):
            output_path += ".img.gz" if compress else ".img"
        unmount_device_partitions(device_path)
        total = dev_info.size_bytes
        copied = 0
        t0 = time.monotonic()
        last_emit = t0
        chunk_size = 4 * 1024 * 1024
        opener = (lambda p: gzip.open(p, "wb", compresslevel=4)) if compress else (lambda p: open(p, "wb"))
        with open(device_path, "rb") as fin, opener(output_path) as fout:
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
        unmount_device_partitions(device_path)
        emit_progress(5, "wipe", f"Effacement des signatures sur {device_path}...")
        run_cmd(["wipefs", "-a", device_path])
        emit_progress(10, "restore", "Restauration de l'image disque vers la clé USB...")
        _write_raw_stream_to_device(
            image_path,
            device_path,
            dev_info.size_bytes,
            stage="restore",
            action_label="Restauration",
        )
        run_cmd(["partprobe", device_path], check=False)
        emit_progress(100, "done", "Image disque restaurée avec succès sur la clé USB !")

    elif action == "usb_benchmark":
        emit_progress(10, "benchmark", f"Mesure du débit de lecture séquentielle sur {device_path}...")
        sample_bytes = min(256 * 1024 * 1024, dev_info.size_bytes)
        read_bytes = 0
        t0 = time.monotonic()
        with open(device_path, "rb") as fin:
            while read_bytes < sample_bytes:
                buf = fin.read(4 * 1024 * 1024)
                if not buf:
                    break
                read_bytes += len(buf)
                emit_progress(10 + (read_bytes / sample_bytes) * 85, "benchmark", "Lecture de l'échantillon 256 Mo...")
        elapsed = max(time.monotonic() - t0, 0.001)
        mbps = (read_bytes / (1024 * 1024)) / elapsed
        emit("benchmark_result", read_mbps=round(mbps, 2), sampled_mb=round(read_bytes / (1024 * 1024), 1))
        emit_progress(100, "done", f"Vitesse de lecture séquentielle mesurée : {mbps:.1f} Mo/s")
    else:
        raise ValueError(f"Action outil inconnue : {action}")


def execute_job_file(job_path: str, dry_run: bool = False) -> None:
    real_job = os.path.realpath(job_path)
    with open(real_job, "r", encoding="utf-8") as f:
        job = json.load(f)

    action = str(job.get("action") or "")
    if sys.platform == "win32":
        from engine.win_backend import (
            execute_win_flash_direct,
            execute_win_toolbox,
            execute_win_ventoy,
        )

        if action == "flash_direct":
            execute_win_flash_direct(
                job,
                dry_run=dry_run,
                emit=emit,
                emit_progress=emit_progress,
                emit_log=emit_log,
                ensure_uefi_ntfs_image=ensure_uefi_ntfs_image,
                copy_tree_with_progress=copy_tree_with_progress,
            )
        elif action in ("ventoy_install", "ventoy_update", "ventoy_apply"):
            execute_win_ventoy(
                job,
                dry_run=dry_run,
                emit=emit,
                emit_progress=emit_progress,
                emit_log=emit_log,
                copy_with_progress=copy_with_progress,
            )
        elif action in ("usb_wipe_reset", "usb_badblocks", "usb_backup_img", "usb_restore_img", "usb_benchmark"):
            execute_win_toolbox(
                job,
                dry_run=dry_run,
                emit=emit,
                emit_progress=emit_progress,
                emit_log=emit_log,
            )
        else:
            raise ValueError(f"Action inconnue dans le job : {action}")
        return

    if action == "flash_direct":
        execute_flash_direct(job, dry_run=dry_run)
    elif action in ("ventoy_install", "ventoy_update", "ventoy_apply"):
        execute_ventoy_operation(job, dry_run=dry_run)
    elif action in ("usb_wipe_reset", "usb_badblocks", "usb_backup_img", "usb_restore_img", "usb_benchmark"):
        execute_toolbox_operation(job, dry_run=dry_run)
    else:
        raise ValueError(f"Action inconnue dans le job : {action}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BootForge System Engine")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("devices", help="List block devices as JSON")
    sub.add_parser("deps", help="Check system dependencies")

    p_ins = sub.add_parser("inspect-iso", help="Inspect ISO/IMG file")
    p_ins.add_argument("path")

    p_hash = sub.add_parser("hash-file", help="Compute hashes of file")
    p_hash.add_argument("path")
    p_hash.add_argument("--expected", default="")
    p_hash.add_argument("--algos", default="sha256,sha1,md5")

    p_vstate = sub.add_parser("ventoy-state", help="Read mounted Ventoy partition state")
    p_vstate.add_argument("mount_point")

    p_xml = sub.add_parser("preview-unattend", help="Preview generated autounattend.xml")
    p_xml.add_argument("--config", required=True)

    p_vjson = sub.add_parser("preview-ventoy-json", help="Preview generated ventoy.json")
    p_vjson.add_argument("--config", required=True)

    p_job = sub.add_parser("run-job", help="Execute a privileged job JSON")
    p_job.add_argument("--job", required=True)
    p_job.add_argument("--dry-run", action="store_true")
    p_job.add_argument("--event-log", default="")

    args = parser.parse_args(argv)
    if getattr(args, "event_log", ""):
        set_event_log_path(args.event_log)
    try:
        if args.cmd == "devices":
            print(json.dumps(list_block_devices(), ensure_ascii=False))
        elif args.cmd == "deps":
            print(json.dumps(check_system_dependencies(), ensure_ascii=False))
        elif args.cmd == "inspect-iso":
            print(json.dumps(inspect_image(args.path), ensure_ascii=False))
        elif args.cmd == "hash-file":
            algos = [a.strip() for a in args.algos.split(",") if a.strip()]
            res = compute_hashes(
                args.path,
                algorithms=algos,
                expected_hash=args.expected or None,
                progress_cb=lambda done, tot, spd: emit_progress((done / max(tot, 1)) * 100, "hash", f"Hachage {done/(1024**2):.0f}/{tot/(1024**2):.0f} Mo", spd),
            )
            emit("hash_result", result=res)
        elif args.cmd == "ventoy-state":
            print(json.dumps(read_ventoy_partition_state(args.mount_point), ensure_ascii=False))
        elif args.cmd == "preview-unattend":
            cfg = json.loads(args.config)
            print(build_autounattend_xml(cfg))
        elif args.cmd == "preview-ventoy-json":
            cfg = json.loads(args.config)
            print(json.dumps(build_ventoy_json(cfg), indent=2, ensure_ascii=False))
        elif args.cmd == "run-job":
            execute_job_file(args.job, dry_run=args.dry_run)
            emit("engine_exit", exit_code=0)
        else:
            parser.print_help()
            return 1
        return 0
    except SafetyViolationError as exc:
        emit("error", code="SAFETY_BLOCKED", message=str(exc))
        emit("engine_exit", exit_code=2)
        return 2
    except Exception as exc:
        emit("error", code="ENGINE_ERROR", message=str(exc))
        emit("engine_exit", exit_code=1)
        return 1
    finally:
        if _EVENT_LOG_FP is not None:
            try:
                _EVENT_LOG_FP.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
