"""Windows async worker threads, UAC elevation runner, and Ventoy reader for BootForge."""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import weakref
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from engine.catalog import default_downloads_dir
from engine.disk_utils import check_system_dependencies, list_block_devices
from engine.iso_inspector import compute_hashes
from engine.net_security import CATALOG_INITIAL_HOSTS, download_https_atomic
from engine.ventoy_manager import read_ventoy_partition_state


class _CallableWorker(QThread):
    finished_ok = pyqtSignal(object)
    finished_err = pyqtSignal(str)

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            res = self._fn()
            self.finished_ok.emit(res)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class AsyncHelper(QObject):
    """Manage background QThreads cleanly without premature GC."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._threads: List[QThread] = []
        self_ref = weakref.ref(self)

        def _on_exit() -> None:
            helper = self_ref()
            if helper is not None:
                helper.wait_all(25000)

        atexit.register(_on_exit)

    def run_fn(self, fn: Callable[[], Any], on_ok: Callable[[Any], None], on_err: Callable[[str], None]) -> None:
        t = _CallableWorker(fn, self)
        self._threads.append(t)

        def _cleanup() -> None:
            if t in self._threads:
                self._threads.remove(t)
            t.deleteLater()

        t.finished_ok.connect(on_ok)
        t.finished_err.connect(on_err)
        t.finished.connect(_cleanup)
        t.start()

    def wait_all(self, timeout_ms: int = 25000) -> None:
        for t in list(self._threads):
            if t.isRunning():
                t.wait(timeout_ms)


def read_ventoy_state_for_device(device: Dict[str, Any]) -> Dict[str, Any]:
    """Read existing Ventoy partition content via mounted drive letter / mountpoint."""
    parts = device.get("partitions") or []
    data_part = next(
        (p for p in parts if str(p.get("label") or "").upper() != "VTOYEFI"),
        parts[0] if parts else None,
    )
    if not data_part:
        raise RuntimeError("Aucune partition trouvée sur cette clé USB.")

    mounts = data_part.get("mountpoints") or []
    mp = mounts[0] if mounts else ""
    if not mp and sys.platform != "win32":
        from bootforge_ui.backend import read_ventoy_state_on_device

        return read_ventoy_state_on_device(device["path"])
    if not mp:
        raise RuntimeError("La partition Ventoy n'a pas de lettre de lecteur attribuée sous Windows.")
    return read_ventoy_partition_state(mp)


class WinTaskController(QObject):
    """Single active job controller for engine execution, hash verification, and ISO downloads."""

    telemetry = pyqtSignal(dict)
    job_done = pyqtSignal(bool, int, str)
    hash_done = pyqtSignal(object, str)
    download_done = pyqtSignal(bool, str)
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._cancel_flag = threading.Event()
        self._active_proc: Optional[subprocess.Popen] = None

    def is_busy(self) -> bool:
        return self._busy

    def cancel(self) -> None:
        self._cancel_flag.set()
        if self._active_proc and self._active_proc.poll() is None:
            try:
                self._active_proc.terminate()
            except OSError:
                pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.busy_changed.emit(busy)

    def run_engine_job(self, job: Dict[str, Any], dry_run: bool) -> bool:
        if self._busy:
            return False
        self._cancel_flag.clear()
        self._set_busy(True)

        def _worker() -> None:
            fd, job_file = tempfile.mkstemp(prefix="bootforge-job-", suffix=".json")
            efd, event_file = tempfile.mkstemp(prefix="bootforge-ev-", suffix=".jsonl")
            os.close(efd)
            exit_code = 1
            msg = ""
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(job, f, indent=2, ensure_ascii=False)

                if dry_run or sys.platform != "win32":
                    from engine.bootforge_engine import execute_job_file, main as engine_main

                    if dry_run:
                        rc = engine_main(["run-job", "--job", job_file, "--dry-run", "--event-log", event_file])
                        self._tail_events_once(event_file)
                        exit_code = rc
                    else:
                        cmd = [sys.executable, str(Path(__file__).resolve().parent.parent / "engine" / "bootforge_engine.py"), "run-job", "--job", job_file]
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                        self._active_proc = proc
                        assert proc.stdout is not None
                        for raw in proc.stdout:
                            self._emit_raw_line(raw)
                        exit_code = proc.wait()
                else:
                    from engine.win_backend import is_windows_admin

                    if is_windows_admin():
                        from engine.bootforge_engine import main as engine_main

                        rc = engine_main(["run-job", "--job", job_file, "--event-log", event_file])
                        self._tail_events_once(event_file)
                        exit_code = rc
                    else:
                        exit_code, msg = self._run_windows_uac_elevated(job_file, event_file)
            except Exception as exc:
                self.telemetry.emit({"type": "error", "code": "RUNNER_ERROR", "message": str(exc)})
                exit_code = 1
                msg = str(exc)
            finally:
                self._active_proc = None
                self._set_busy(False)
                for p in (job_file, event_file):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                self.job_done.emit(exit_code == 0, exit_code, msg)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def _emit_raw_line(self, raw: str) -> None:
        line = raw.strip()
        if not line:
            return
        try:
            self.telemetry.emit(json.loads(line))
        except json.JSONDecodeError:
            self.telemetry.emit({"type": "log", "level": "stdout", "message": line})

    def _tail_events_once(self, event_file: str) -> None:
        try:
            with open(event_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    self._emit_raw_line(line)
        except OSError:
            pass

    def _run_windows_uac_elevated(self, job_file: str, event_file: str) -> tuple[int, str]:
        """Launch hidden UAC-elevated worker and stream JSONL telemetry events live."""
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = f'--engine-job "{job_file}" --event-log "{event_file}"'
        else:
            launcher = str(Path(__file__).resolve().parent.parent / "bootforge")
            exe = sys.executable
            params = f'"{launcher}" --engine-job "{job_file}" --event-log "{event_file}"'

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("fMask", ctypes.c_ulong),
                ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.c_void_p),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p),
                ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_ulong),
                ("hIcon", ctypes.c_void_p),
                ("hProcess", ctypes.c_void_p),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        WAIT_OBJECT_0 = 0
        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = exe
        sei.lpParameters = params
        sei.nShow = 0  # SW_HIDE

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.GetLastError()
            if err == 1223:
                return 126, "Authentification administrateur Windows annulée."
            return 1, f"Impossible de lancer le worker élevé (Win32={err})."

        h_proc = sei.hProcess
        kernel32 = ctypes.windll.kernel32
        pos = 0
        exit_code_c = ctypes.c_ulong(1)

        try:
            while True:
                if self._cancel_flag.is_set() and h_proc:
                    kernel32.TerminateProcess(h_proc, 1)
                if os.path.exists(event_file):
                    with open(event_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    for line in chunk.splitlines():
                        self._emit_raw_line(line)

                if not h_proc:
                    break
                wait_rc = kernel32.WaitForSingleObject(h_proc, 120)
                if wait_rc == WAIT_OBJECT_0:
                    # Flush any remaining lines
                    if os.path.exists(event_file):
                        with open(event_file, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(pos)
                            for line in f:
                                self._emit_raw_line(line)
                    kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code_c))
                    break
        finally:
            if h_proc:
                kernel32.CloseHandle(h_proc)

        return int(exit_code_c.value), ""

    def run_hash_check(self, file_path: str, expected_hash: str) -> bool:
        if self._busy:
            return False
        self._cancel_flag.clear()
        self._set_busy(True)

        def _worker() -> None:
            res = None
            err = ""
            try:
                res = compute_hashes(
                    file_path,
                    algorithms=["sha256", "sha1", "md5"],
                    expected_hash=expected_hash.strip() or None,
                    progress_cb=lambda done, tot, spd: self.telemetry.emit(
                        {
                            "type": "progress",
                            "percent": round((done / max(tot, 1)) * 100.0, 1),
                            "stage": "hash",
                            "speed_bps": spd,
                            "message": f"Analyse : {done // (1024**2)} / {tot // (1024**2)} Mo",
                        }
                    ),
                )
            except Exception as exc:
                err = str(exc)
            finally:
                self._set_busy(False)
                self.hash_done.emit(res, err)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def run_catalog_download(self, item: Dict[str, str], dest_dir: str) -> bool:
        if self._busy:
            return False
        self._cancel_flag.clear()
        self._set_busy(True)

        def _worker() -> None:
            ok = False
            msg = ""
            try:
                url = str(item.get("url") or "")
                parsed = urllib.parse.urlparse(url)
                raw_name = os.path.basename(parsed.path) or f"{item.get('id', 'image')}.iso"
                safe_name = "".join(c if (c.isalnum() or c in "._-") else "_" for c in raw_name).lstrip(".") or f"{item.get('id', 'image')}.iso"
                target_dir = Path(dest_dir or str(default_downloads_dir())).resolve()
                target_dir.mkdir(parents=True, exist_ok=True)
                dest_path = target_dir / safe_name

                def _on_progress(received: int, total: int, speed: float) -> None:
                    pct = (received / total * 100.0) if total > 0 else 0.0
                    self.telemetry.emit(
                        {
                            "type": "progress",
                            "percent": round(pct, 1),
                            "stage": "download",
                            "speed_bps": speed,
                            "message": f"Téléchargement {safe_name} : {received / (1024**2):.1f} Mo"
                            + (f" / {total / (1024**2):.0f} Mo" if total else ""),
                        }
                    )

                expected_sha = str(item.get("sha256") or "").strip().lower() or None
                download_https_atomic(
                    url,
                    dest_path,
                    allowed_hosts=CATALOG_INITIAL_HOSTS,
                    allow_any_https_redirect=True,
                    expected_sha256=expected_sha,
                    timeout=45,
                    cancel_check=self._cancel_flag.is_set,
                    progress_cb=_on_progress,
                )

                self.telemetry.emit(
                    {"type": "progress", "percent": 100.0, "stage": "done", "message": f"Téléchargement terminé : {dest_path}"}
                )
                ok = True
                msg = str(dest_path)
            except Exception as exc:
                ok = False
                msg = str(exc)
            finally:
                self._set_busy(False)
                self.download_done.emit(ok, msg)

        threading.Thread(target=_worker, daemon=True).start()
        return True
