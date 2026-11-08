"""Non-blocking background task runner & system integration for BootForge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = PROJECT_ROOT / "engine" / "bootforge_engine.py"

DEVICE_RE = re.compile(r"^/dev/(sd[a-z]+|vd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")
PARTITION_RE = re.compile(r"^/dev/(sd[a-z]+\d+|vd[a-z]+\d+|hd[a-z]+\d+|nvme\d+n\d+p\d+|mmcblk\d+p\d+)$")

OFFICIAL_CATALOG: List[Dict[str, str]] = [
    {
        "id": "ubuntu-2404",
        "name": "Ubuntu Desktop 24.04.2 LTS",
        "version": "24.04.2 LTS",
        "category": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "description": "Distribution LTS stable avec prise en charge de la persistance casper-rw.",
        "simple_description": "Système Linux populaire, simple à utiliser au quotidien.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~5.8 Go",
        "url": "https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-desktop-amd64.iso",
        "checksum_page": "https://releases.ubuntu.com/24.04.2/SHA256SUMS",
        "persistence_hint": "casper-rw",
    },
    {
        "id": "mint-cinnamon",
        "name": "Linux Mint 22.1 Cinnamon",
        "version": "22.1 Xia",
        "category": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "description": "Environnement classique léger, rapide et compatible avec la majorité des PC.",
        "simple_description": "Interface familière proche de Windows, idéale pour débuter sous Linux.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~2.9 Go",
        "url": "https://mirrors.kernel.org/linuxmint/stable/22.1/linuxmint-22.1-cinnamon-64bit.iso",
        "checksum_page": "https://mirrors.kernel.org/linuxmint/stable/22.1/sha256sum.txt",
        "persistence_hint": "casper-rw",
    },
    {
        "id": "debian-live",
        "name": "Debian 12 Live GNOME",
        "version": "12.9 Bookworm",
        "category": "Linux Desktop",
        "simple_category": "Systèmes courants",
        "description": "Base Debian stable pour dépannage, bureautique ou environnement Live.",
        "simple_description": "Système léger et très fiable pour travailler ou dépanner un ordinateur.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~3.3 Go",
        "url": "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/debian-live-12.9.0-amd64-gnome.iso",
        "checksum_page": "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/SHA256SUMS",
        "persistence_hint": "debian-persistence",
    },
    {
        "id": "systemrescue",
        "name": "SystemRescue",
        "version": "11.03",
        "category": "Secours & Disque",
        "simple_category": "Dépannage",
        "description": "Boîte à outils d’urgence : GParted, Clonezilla, testdisk, chroot, réparations EFI.",
        "simple_description": "Outil de secours pour récupérer des fichiers ou réparer un PC en panne.",
        "architecture": "x86_64",
        "size_hint": "~980 Mo",
        "url": "https://fastly-cdn.system-rescue.org/releases/11.03/systemrescue-11.03-amd64.iso",
        "checksum_page": "https://www.system-rescue.org/Download/",
        "persistence_hint": "none",
    },
    {
        "id": "gparted-live",
        "name": "GParted Live",
        "version": "1.7.0-1",
        "category": "Secours & Disque",
        "simple_category": "Dépannage",
        "description": "Gestionnaire de partitions dédié pour redimensionner et réparer vos disques.",
        "simple_description": "Utilitaire simple pour organiser, réduire ou effacer des disques durs.",
        "architecture": "x86_64",
        "size_hint": "~560 Mo",
        "url": "https://downloads.sourceforge.net/gparted/gparted-live-1.7.0-1-amd64.iso",
        "checksum_page": "https://gparted.org/download.php",
        "persistence_hint": "none",
    },
    {
        "id": "kali-live",
        "name": "Kali Linux Live",
        "version": "2026.x Rolling",
        "category": "Sécurité & Pentest",
        "simple_category": "Sécurité",
        "description": "Système Live audit réseau et sécurité avec persistance Debian (/ union).",
        "simple_description": "Système spécialisé pour les audits réseau et l’analyse de sécurité.",
        "architecture": "x86_64 (amd64)",
        "size_hint": "~4.4 Go",
        "url": "https://cdimage.kali.org/current/kali-linux-2026.1-live-amd64.iso",
        "checksum_page": "https://cdimage.kali.org/current/SHA256SUMS",
        "persistence_hint": "debian-persistence",
    },
]


def default_downloads_dir() -> Path:
    for candidate in (Path.home() / "Téléchargements", Path.home() / "Downloads", Path.home()):
        if candidate.is_dir():
            return candidate
    return Path.home()


def run_async(
    fn: Callable[[], Any],
    on_success: Callable[[Any], None],
    on_error: Callable[[str], None],
) -> None:
    """Execute blocking function in daemon thread and dispatch result on GLib main loop."""

    def _worker() -> None:
        try:
            res = fn()
        except Exception as exc:
            err_msg = str(exc)
            GLib.idle_add(on_error, err_msg)
            return
        GLib.idle_add(on_success, res)

    threading.Thread(target=_worker, daemon=True).start()


def read_ventoy_state_on_device(device_path: str) -> Dict[str, Any]:
    """Mount (via udisksctl if unmounted) and inspect partition 1 of a Ventoy USB drive."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from engine.disk_utils import list_block_devices
    from engine.ventoy_manager import read_ventoy_partition_state

    if not DEVICE_RE.match(device_path):
        raise ValueError(f"Chemin de périphérique invalide : {device_path}")

    devices = list_block_devices()
    dev = next((d for d in devices if d["path"] == device_path), None)
    if not dev:
        raise RuntimeError(f"Périphérique introuvable : {device_path}")

    parts = dev.get("partitions") or []
    if not parts:
        raise RuntimeError("Aucune partition trouvée sur cette clé USB.")

    data_part = next(
        (p for p in parts if str(p.get("label") or "").upper() != "VTOYEFI"),
        parts[0],
    )
    part_path = str(data_part.get("path") or "")
    mounts = data_part.get("mountpoints") or []
    mount_point = mounts[0] if mounts else ""

    if not mount_point:
        if not PARTITION_RE.match(part_path):
            raise RuntimeError(f"Chemin de partition invalide : {part_path}")
        proc = subprocess.run(
            ["udisksctl", "mount", "-b", part_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Échec montage udisksctl")
        m = re.search(r"Mounted\s+\S+\s+at\s+(.+)\.?$", proc.stdout, flags=re.M)
        if m:
            mount_point = m.group(1).strip().rstrip(".")

    if not mount_point:
        refreshed = list_block_devices()
        rdev = next((d for d in refreshed if d["path"] == device_path), None)
        if rdev:
            for p in rdev.get("partitions") or []:
                if p.get("path") == part_path and p.get("mountpoints"):
                    mount_point = p["mountpoints"][0]
                    break

    if not mount_point:
        raise RuntimeError("Impossible de monter la partition Ventoy pour lecture.")

    return read_ventoy_partition_state(mount_point)


class ActiveProcessController:
    """Thread-safe controller managing one active engine job, hash job, or ISO download."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._busy = False
        self._cancel_flag = threading.Event()

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancel(self) -> bool:
        self._cancel_flag.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            return True
        return True

    def run_engine_job(
        self,
        job: Dict[str, Any],
        dry_run: bool,
        on_telemetry: Callable[[Dict[str, Any]], None],
        on_done: Callable[[bool, int], None],
    ) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._cancel_flag.clear()

        def _worker() -> None:
            fd, job_file = tempfile.mkstemp(prefix="bootforge-job-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(job, f, indent=2, ensure_ascii=False)
                os.chmod(job_file, 0o600)

                engine_args = [str(ENGINE_PATH), "run-job", "--job", job_file]
                if dry_run:
                    cmd = [sys.executable, *engine_args, "--dry-run"]
                elif str(ENGINE_PATH) == "/usr/share/bootforge/engine/bootforge_engine.py":
                    cmd = ["pkexec", *engine_args]
                else:
                    cmd = ["pkexec", sys.executable, *engine_args]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                with self._lock:
                    self._proc = proc

                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        GLib.idle_add(on_telemetry, ev)
                    except json.JSONDecodeError:
                        GLib.idle_add(on_telemetry, {"type": "log", "level": "stdout", "message": line})

                exit_code = proc.wait()
                if exit_code in (126, 127) and not dry_run:
                    GLib.idle_add(
                        on_telemetry,
                        {
                            "type": "error",
                            "code": "AUTH_CANCELLED",
                            "message": "Authentification administrateur annulée ou refusée.",
                        },
                    )
                GLib.idle_add(on_done, exit_code == 0, exit_code)
            except Exception as exc:
                GLib.idle_add(on_telemetry, {"type": "error", "code": "RUNNER_ERROR", "message": str(exc)})
                GLib.idle_add(on_done, False, 1)
            finally:
                with self._lock:
                    self._proc = None
                    self._busy = False
                self._cancel_flag.clear()
                try:
                    os.unlink(job_file)
                except OSError:
                    pass

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def run_hash_check(
        self,
        file_path: str,
        expected_hash: str,
        on_telemetry: Callable[[Dict[str, Any]], None],
        on_done: Callable[[Optional[Dict[str, Any]], Optional[str]], None],
    ) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._cancel_flag.clear()

        def _worker() -> None:
            try:
                args = [sys.executable, str(ENGINE_PATH), "hash-file", file_path]
                if expected_hash.strip():
                    args.extend(["--expected", expected_hash.strip()])
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                with self._lock:
                    self._proc = proc

                hash_res: Optional[Dict[str, Any]] = None
                err_msg: Optional[str] = None
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        GLib.idle_add(on_telemetry, ev)
                        if ev.get("type") == "hash_result":
                            hash_res = ev.get("result")
                        elif ev.get("type") == "error":
                            err_msg = str(ev.get("message") or "Erreur hachage")
                    except json.JSONDecodeError:
                        GLib.idle_add(on_telemetry, {"type": "log", "level": "stdout", "message": line})

                rc = proc.wait()
                if rc != 0 and not err_msg:
                    err_msg = f"Calcul interrompu (code {rc})"
                GLib.idle_add(on_done, hash_res, err_msg)
            except Exception as exc:
                GLib.idle_add(on_done, None, str(exc))
            finally:
                with self._lock:
                    self._proc = None
                    self._busy = False
                self._cancel_flag.clear()

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def run_catalog_download(
        self,
        item: Dict[str, str],
        dest_dir: str,
        on_telemetry: Callable[[Dict[str, Any]], None],
        on_done: Callable[[bool, str], None],
    ) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._cancel_flag.clear()

        def _worker() -> None:
            dest_path: Optional[Path] = None
            try:
                url = item.get("url", "")
                if not url.startswith("https://"):
                    raise ValueError("URL HTTPS requise.")

                parsed = urllib.parse.urlparse(url)
                safe_name = os.path.basename(parsed.path) or f"{item.get('id', 'image')}.iso"
                target_dir = Path(dest_dir or str(default_downloads_dir())).resolve()
                target_dir.mkdir(parents=True, exist_ok=True)
                dest_path = target_dir / safe_name

                req = urllib.request.Request(url, headers={"User-Agent": "BootForge/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    final_scheme = urllib.parse.urlparse(resp.geturl()).scheme
                    if final_scheme not in ("https", "http"):
                        raise RuntimeError("Protocole de redirection non autorisé.")
                    total = int(resp.headers.get("Content-Length") or 0)
                    received = 0
                    t0 = time.monotonic()
                    last_emit = t0
                    with open(dest_path, "wb") as fout:
                        while not self._cancel_flag.is_set():
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            fout.write(chunk)
                            received += len(chunk)
                            now = time.monotonic()
                            if now - last_emit >= 0.25:
                                elapsed = max(now - t0, 0.01)
                                speed = received / elapsed
                                pct = (received / total * 100.0) if total > 0 else 0.0
                                GLib.idle_add(
                                    on_telemetry,
                                    {
                                        "type": "progress",
                                        "percent": round(pct, 1),
                                        "stage": "download",
                                        "speed_bps": speed,
                                        "message": (
                                            f"Téléchargement {safe_name} : {received / (1024**2):.1f} Mo"
                                            + (f" / {total / (1024**2):.0f} Mo" if total else "")
                                        ),
                                    },
                                )
                                last_emit = now

                if self._cancel_flag.is_set():
                    if dest_path:
                        dest_path.unlink(missing_ok=True)
                    GLib.idle_add(on_done, False, "Téléchargement annulé.")
                    return

                GLib.idle_add(
                    on_telemetry,
                    {"type": "progress", "percent": 100.0, "stage": "done", "message": f"Téléchargement terminé : {dest_path}"},
                )
                GLib.idle_add(on_done, True, str(dest_path))
            except Exception as exc:
                if dest_path:
                    dest_path.unlink(missing_ok=True)
                GLib.idle_add(on_done, False, str(exc))
            finally:
                with self._lock:
                    self._busy = False
                self._cancel_flag.clear()

        threading.Thread(target=_worker, daemon=True).start()
        return True


def launch_qemu_preview(device_path: str) -> tuple[bool, str]:
    """Launch QEMU OVMF/BIOS read-only snapshot boot preview detached from UI."""
    if not DEVICE_RE.match(device_path):
        return False, "Chemin de périphérique invalide."
    if not shutil.which("qemu-system-x86_64"):
        return False, "QEMU n’est pas installé. Exécutez : sudo apt install qemu-system-x86 ovmf"

    ovmf_candidates = (
        "/usr/share/OVMF/OVMF_CODE.fd",
        "/usr/share/OVMF/OVMF_CODE_4M.fd",
        "/usr/share/ovmf/OVMF.fd",
        "/usr/share/qemu/OVMF.fd",
    )
    ovmf_path = next((c for c in ovmf_candidates if os.path.exists(c)), None)
    has_kvm = os.path.exists("/dev/kvm")

    cmd = ["pkexec", "qemu-system-x86_64", "-m", "2048", "-smp", "2"]
    if has_kvm:
        cmd.append("-enable-kvm")
    cmd.extend(["-drive", f"file={device_path},format=raw,snapshot=on"])
    if ovmf_path:
        cmd.extend(["-bios", ovmf_path])

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    mode_txt = "UEFI OVMF" if ovmf_path else "BIOS Legacy"
    return True, f"Aperçu QEMU lancé en mode lecture seule (-snapshot) · {mode_txt}."
