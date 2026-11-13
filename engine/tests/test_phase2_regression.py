#!/usr/bin/env python3
"""Phase 2 negative, edge-case, and regression test suite for BootForge."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine import bootforge_engine, disk_utils, net_security, updater, ventoy_manager, win_backend


def _load_sign_release():
    path = PROJECT_ROOT / "packaging" / "sign_release.py"
    spec = importlib.util.spec_from_file_location("sign_release_p2", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_usb_dev(
    path: str = "/dev/sdb",
    size_bytes: int = 16 * 1024**3,
    *,
    ro: bool = False,
    transport: str = "usb",
    removable: bool = True,
    hotplug: bool = True,
    partitions: list[disk_utils.PartitionInfo] | None = None,
) -> disk_utils.BlockDeviceInfo:
    return disk_utils.BlockDeviceInfo(
        name=os.path.basename(path),
        path=path,
        size_bytes=size_bytes,
        model="TestUSB",
        vendor="BootForge",
        serial="BF1234",
        transport=transport,
        removable=removable,
        read_only=ro,
        hotplug=hotplug,
        pttype="gpt",
        usb_speed_mbps=5000,
        vid_pid="1234:5678",
        is_system_disk=False,
        system_reasons=[],
        ventoy_detected=False,
        partitions=partitions or [],
    )


class Phase2RegressionTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # disk_utils.py negative & disappearance / error tests
    # ------------------------------------------------------------------
    def test_disk_utils_rejects_traversal_ro_partition_and_disappearing_usb(self) -> None:
        nvme_sys = disk_utils.BlockDeviceInfo(
            name="nvme0n1",
            path="/dev/nvme0n1",
            size_bytes=512 * 1024**3,
            model="SysNVMe",
            vendor="NVMe",
            serial="SYS",
            transport="nvme",
            removable=False,
            read_only=False,
            hotplug=False,
            pttype="gpt",
            usb_speed_mbps=None,
            vid_pid=None,
            is_system_disk=True,
            system_reasons=["Montage système critique : /"],
            ventoy_detected=False,
            partitions=[
                disk_utils.PartitionInfo(
                    name="nvme0n1p1",
                    path="/dev/nvme0n1p1",
                    size_bytes=100,
                    fstype="ext4",
                    label="root",
                    uuid="1111-2222",
                    mountpoints=["/"],
                )
            ],
        )
        usb_ro = _make_usb_dev("/dev/sdb", ro=True)

        # Path traversal non-existent path -> normpath yields /dev/nvme0n1 -> blocked as system disk
        with self.assertRaises(disk_utils.SafetyViolationError):
            disk_utils.validate_target_device("/dev/foo/../../dev/nvme0n1", devices=[nvme_sys, usb_ro])

        # Partition path explicitly blocked
        with self.assertRaises(disk_utils.SafetyViolationError):
            disk_utils.validate_target_device("/dev/sdb1", devices=[usb_ro])
        with self.assertRaises(disk_utils.SafetyViolationError):
            disk_utils.validate_target_device("/dev/nvme0n1p1", devices=[nvme_sys])

        # Read-only USB blocked
        with self.assertRaises(disk_utils.SafetyViolationError):
            disk_utils.validate_target_device("/dev/sdb", devices=[usb_ro])

        # Disappeared USB (not in live lsblk output)
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["lsblk"], 0, stdout='{"blockdevices": []}', stderr="")):
            with self.assertRaises(disk_utils.SafetyViolationError):
                disk_utils.validate_target_device("/dev/sdb")

        # Corrupted lsblk JSON / timeout propagates cleanly without silent ignore
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["lsblk"], 0, stdout="{broken", stderr="")):
            with self.assertRaises(json.JSONDecodeError):
                disk_utils.list_block_devices()
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("lsblk", 5)):
            with self.assertRaises(subprocess.TimeoutExpired):
                disk_utils.validate_target_device("/dev/sdb")

    # ------------------------------------------------------------------
    # net_security.py interrupted download, cancellation & size limits
    # ------------------------------------------------------------------
    def test_net_security_interrupted_stream_cancelled_and_max_bytes(self) -> None:
        class _ChunkResp:
            headers = {"Content-Length": "100"}

            def __init__(self, chunks: list[bytes], fail_after: bool = False):
                self._chunks = list(chunks)
                self._fail = fail_after

            def geturl(self) -> str:
                return "https://github.com/NiceMank/BootForge/releases/download/v1.0.1/a.bin"

            def read(self, _n: int = -1) -> bytes:
                if self._chunks:
                    return self._chunks.pop(0)
                if self._fail:
                    raise OSError("Connection reset by peer")
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "artifact.bin"
            dest.write_bytes(b"ORIGINAL_KEEP")

            # 1. Interrupted socket read preserves existing target and deletes .part
            with mock.patch("urllib.request.OpenerDirector.open", return_value=_ChunkResp([b"abc"], fail_after=True)):
                with self.assertRaises(OSError):
                    net_security.download_https_atomic(
                        "https://github.com/NiceMank/BootForge/releases/download/v1.0.1/a.bin",
                        dest,
                        allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS,
                    )
            self.assertEqual(dest.read_bytes(), b"ORIGINAL_KEEP")
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

            # 2. User cancellation aborts cleanly and deletes .part
            calls = [0]

            def _cancel_after_one() -> bool:
                calls[0] += 1
                return calls[0] >= 1

            with mock.patch("urllib.request.OpenerDirector.open", return_value=_ChunkResp([b"chunk1", b"chunk2"])):
                with self.assertRaises(InterruptedError):
                    net_security.download_https_atomic(
                        "https://github.com/NiceMank/BootForge/releases/download/v1.0.1/a.bin",
                        dest,
                        allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS,
                        cancel_check=_cancel_after_one,
                    )
            self.assertEqual(dest.read_bytes(), b"ORIGINAL_KEEP")
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

            # 3. Oversized response in fetch_https_bytes raises ValueError
            with mock.patch("urllib.request.OpenerDirector.open", return_value=_ChunkResp([b"X" * 100])):
                with self.assertRaises(ValueError):
                    net_security.fetch_https_bytes(
                        "https://api.github.com/repos/NiceMank/BootForge/releases/latest",
                        allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS,
                        max_bytes=50,
                    )

        # 4. Redirect to untrusted host blocked
        handler = net_security._StrictHttpsRedirectHandler(net_security.GITHUB_ALLOWED_HOSTS)
        req = net_security.urllib.request.Request("https://github.com/NiceMank/BootForge")
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/payload.deb")

    # ------------------------------------------------------------------
    # updater.py & sign_release.py Phase 2 regressions
    # ------------------------------------------------------------------
    def test_updater_raw_sig_whitespace_small_order_url_spoof_and_install_flags(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        # 1. Production public key is provisioned and unprovisioned keymaps still return False
        self.assertEqual(updater.PRODUCTION_KEY_ID, "bootforge-release-2026")
        self.assertTrue(updater.is_release_key_provisioned())
        self.assertIsNotNone(updater._ed_decode_point(bytes.fromhex(updater.TRUSTED_RELEASE_PUBLIC_KEYS[updater.PRODUCTION_KEY_ID])))
        self.assertFalse(updater.is_release_key_provisioned(trusted_keys={updater.PRODUCTION_KEY_ID: ""}))

        # 2. Small-order public key (canonical order-2 point (0, p-1)) must be rejected even with crafted r/s
        small_order_pub = bytes([0xEC] + [0xFF] * 30 + [0x7F])
        self.assertFalse(updater.ed25519_verify(small_order_pub, b"msg", b"\x00" * 64))

        # 3. Raw 64-byte binary signature starting/ending with 0x20 / 0x0a is NOT stripped!
        raw_sig_ws = b"\x20" + bytes(range(62)) + b"\x0a"
        self.assertEqual(len(raw_sig_ws), 64)
        self.assertEqual(updater.decode_signature_bytes(raw_sig_ws), raw_sig_ws)

        # 4. Cross-repo GitHub asset URL spoofing inside otherwise validly-signed manifest is blocked
        priv = Ed25519PrivateKey.generate()
        pub_hex = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        spoof_manifest = json.dumps(
            {
                "schema_version": 1,
                "product": "BootForge",
                "repo": "NiceMank/BootForge",
                "key_id": "bootforge-release-2026",
                "version": "1.5.0",
                "tag": "v1.5.0",
                "assets": {
                    "linux_deb": {
                        "name": "bootforge_1.5.0_all.deb",
                        "url": "https://github.com/AttackerFork/EvilForge/releases/download/v1.5.0/bootforge_1.5.0_all.deb",
                        "sha256": "a" * 64,
                        "size": 123,
                    }
                },
            }
        ).encode("utf-8")
        spoof_sig = base64.b64encode(priv.sign(spoof_manifest)).decode("ascii").encode("ascii")
        rel_json = json.dumps(
            {
                "tag_name": "v1.5.0",
                "assets": [
                    {"name": "BOOTFORGE_MANIFEST.json", "browser_download_url": "https://github.com/NiceMank/BootForge/releases/download/v1.5.0/BOOTFORGE_MANIFEST.json"},
                    {"name": "BOOTFORGE_MANIFEST.json.sig", "browser_download_url": "https://github.com/NiceMank/BootForge/releases/download/v1.5.0/BOOTFORGE_MANIFEST.json.sig"},
                ],
            }
        ).encode("utf-8")

        def _fetch(url: str, **_kw) -> bytes:
            if url.endswith("/releases/latest"):
                return rel_json
            if url.endswith("BOOTFORGE_MANIFEST.json"):
                return spoof_manifest
            if url.endswith("BOOTFORGE_MANIFEST.json.sig"):
                return spoof_sig
            raise AssertionError(url)

        with mock.patch("engine.updater.fetch_https_bytes", side_effect=_fetch):
            res_spoof = updater.check_for_update(
                current_version="1.0.1",
                trusted_keys={"bootforge-release-2026": pub_hex},
                install_channel="linux_deb",
            )
            # Provisioned production key MUST reject manifest signed by any non-production key
            res_prod_rejects = updater.check_for_update(
                current_version="1.0.1",
                install_channel="linux_deb",
            )
        self.assertEqual(res_spoof.status, "invalid_signature")
        self.assertFalse(res_spoof.verified)
        self.assertEqual(res_prod_rejects.status, "invalid_signature")
        self.assertFalse(res_prod_rejects.verified)

        # 5. apply_downloaded_update: linux_deb uses APT::Sandbox::User=root when apt-get present; windows_setup uses NSIS /S
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            deb_p = tmp_p / "bootforge_1.5.0_all.deb"
            deb_p.write_bytes(b"DEB")
            upd_deb = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.5.0",
                tag_name="v1.5.0",
                verified=True,
                asset_kind="linux_deb",
                asset_name=deb_p.name,
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.5.0/bootforge_1.5.0_all.deb",
                asset_sha256=hashlib.sha256(b"DEB").hexdigest(),
            )
            seen_deb: list[list[str]] = []
            with mock.patch("shutil.which", return_value="/usr/bin/apt-get"):
                updater.apply_downloaded_update(deb_p, upd_deb, runner=seen_deb.append)
            self.assertIn("APT::Sandbox::User=root", seen_deb[0])

            exe_p = tmp_p / "BootForge-1.5.0-win64-setup.exe"
            exe_p.write_bytes(b"MZ")
            upd_win = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.5.0",
                tag_name="v1.5.0",
                verified=True,
                asset_kind="windows_setup",
                asset_name=exe_p.name,
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.5.0/BootForge-1.5.0-win64-setup.exe",
                asset_sha256=hashlib.sha256(b"MZ").hexdigest(),
            )
            seen_win: list[list[str]] = []
            updater.apply_downloaded_update(exe_p, upd_win, runner=seen_win.append)
            self.assertEqual(seen_win[0], [str(exe_p.resolve()), "/S"])

    def test_sign_release_keygen_safety_and_require_signature(self) -> None:
        sign_release = _load_sign_release()

        # Refuses --keygen without --private-out or --unsafe-stdout
        self.assertEqual(sign_release.main(["--keygen"]), 2)

        # Refuses writing private key inside git repository
        inside_repo = PROJECT_ROOT / "packaging" / ".forbidden_priv.key"
        self.assertEqual(sign_release.main(["--keygen", "--private-out", str(inside_repo)]), 2)
        self.assertFalse(inside_repo.exists())

        # Writes mode 0600 outside repo and never crashes
        with tempfile.TemporaryDirectory() as outside_tmp:
            priv_file = Path(outside_tmp) / "prod_release.key"
            rc = sign_release.main(["--keygen", "--private-out", str(priv_file)])
            self.assertEqual(rc, 0)
            self.assertTrue(priv_file.is_file())
            self.assertEqual(stat.S_IMODE(priv_file.stat().st_mode), 0o600)
            self.assertEqual(len(base64.b64decode(priv_file.read_text().strip())), 32)

            # --require-signature fails if signing key missing
            dist_dir = Path(outside_tmp) / "dist"
            dist_dir.mkdir()
            (dist_dir / "bootforge_1.0.1_all.deb").write_bytes(b"deb")
            with mock.patch.dict(os.environ, {"BOOTFORGE_SIGNING_KEY": ""}):
                rc_req = sign_release.main(["--dist-dir", str(dist_dir), "--tag", "v1.0.1", "--require-signature"])
            self.assertEqual(rc_req, 3)

    # ------------------------------------------------------------------
    # ventoy_manager.py negative & corruption tests
    # ------------------------------------------------------------------
    def test_ventoy_corrupted_archives_hardlinks_and_missing_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)

            # Corrupted tarball
            bad_tar = tmp_p / "bad.tar.gz"
            bad_tar.write_bytes(b"NOT A TARBALL")
            with self.assertRaises(tarfile.TarError):
                ventoy_manager.safe_extract_tar(bad_tar, tmp_p / "out_tar")

            # Corrupted zip
            bad_zip = tmp_p / "bad.zip"
            bad_zip.write_bytes(b"NOT A ZIP")
            with self.assertRaises(zipfile.BadZipFile):
                ventoy_manager.safe_extract_zip(bad_zip, tmp_p / "out_zip")

            # Hardlink inside tar rejected
            hl_tar = tmp_p / "hardlink.tar.gz"
            with tarfile.open(hl_tar, "w:gz") as tf:
                ti = tarfile.TarInfo("evil_link")
                ti.type = tarfile.LNKTYPE
                ti.linkname = "/etc/passwd"
                tf.addfile(ti)
            with self.assertRaises(ValueError):
                ventoy_manager.safe_extract_tar(hl_tar, tmp_p / "out_hl")

            # Missing SHA-256 asset fails closed
            with mock.patch("engine.ventoy_manager.find_cached_ventoy_dir", return_value=None), mock.patch(
                "engine.ventoy_manager.get_ventoy_cache_dir", return_value=tmp_p
            ), mock.patch(
                "engine.ventoy_manager.fetch_https_bytes",
                return_value=json.dumps({"assets": [{"name": "ventoy-1.1.05-linux.tar.gz", "browser_download_url": "https://github.com/ventoy/Ventoy/releases/download/v1.1.05/ventoy-1.1.05-linux.tar.gz"}]}).encode("utf-8"),
            ):
                with self.assertRaises(RuntimeError):
                    ventoy_manager.download_and_extract_ventoy()

            # PermissionError creating cache dir propagates cleanly
            with mock.patch("engine.ventoy_manager.find_cached_ventoy_dir", return_value=None), mock.patch(
                "engine.ventoy_manager.get_ventoy_cache_dir", side_effect=PermissionError("Read-only FS")
            ):
                with self.assertRaises(PermissionError):
                    ventoy_manager.download_and_extract_ventoy()

    # ------------------------------------------------------------------
    # bootforge_engine.py interrupted / overflow / umount-failure tests
    # ------------------------------------------------------------------
    def test_bootforge_engine_raw_stream_limits_and_safe_umount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)

            # 1. Compressed .gz bomb / overflow exceeds dev_size_bytes -> fails BEFORE exceeding device
            gz_img = tmp_p / "disk.img.gz"
            with gzip.open(gz_img, "wb") as gf:
                gf.write(b"A" * 4096)
            target_dev = tmp_p / "fake_blk"
            with self.assertRaises(RuntimeError):
                bootforge_engine._write_raw_stream_to_device(str(gz_img), str(target_dev), dev_size_bytes=1024)

            # 2. Safe .xz write within limit succeeds
            xz_img = tmp_p / "ok.img.xz"
            with lzma.open(xz_img, "wb") as xf:
                xf.write(b"BOOTFORGE" * 128)
            bootforge_engine._write_raw_stream_to_device(str(xz_img), str(target_dev), dev_size_bytes=65536)
            self.assertEqual(target_dev.read_bytes(), b"BOOTFORGE" * 128)

            # 3. _safe_umount_cleanup preserves mounted dir contents when umount fails!
            mnt_dir = tmp_p / "still_mounted"
            mnt_dir.mkdir()
            sentinel = mnt_dir / "precious_user_file.txt"
            sentinel.write_text("KEEP", encoding="utf-8")
            with mock.patch("engine.bootforge_engine.run_cmd", return_value=subprocess.CompletedProcess(["umount"], 32, "", "target is busy")):
                bootforge_engine._safe_umount_cleanup(str(mnt_dir))
            self.assertTrue(sentinel.is_file())

            # 4. Missing file or missing wimlib error path
            missing_job = tmp_p / "missing.json"
            missing_job.write_text(json.dumps({"action": "flash_direct", "device": "/dev/sdb", "image_path": str(tmp_p / "nope.iso")}))
            with self.assertRaises(FileNotFoundError):
                bootforge_engine.execute_job_file(str(missing_job), dry_run=True)

    # ------------------------------------------------------------------
    # win_backend.py diskpart/PS errors, handle cleanup, badblocks & restore bounds
    # ------------------------------------------------------------------
    def test_win_backend_error_handling_lock_cleanup_badblocks_and_overflow(self) -> None:
        # 1. _run_diskpart non-zero exit & TimeoutExpired clean temp script file
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["diskpart"], 5, "ERR", "Access Denied")):
            with self.assertRaises(RuntimeError):
                win_backend._run_diskpart(["select disk 2", "clean", "exit"], lambda *a: None)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("diskpart", 180)):
            with self.assertRaises(subprocess.TimeoutExpired):
                win_backend._run_diskpart(["list disk"], lambda *a: None)

        # 2. _run_powershell_json non-zero exit & bad JSON
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["powershell"], 1, "", "PS error")):
            with self.assertRaises(RuntimeError):
                win_backend._run_powershell_json("Get-Disk")
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(["powershell"], 0, "{not-json", "")):
            with self.assertRaises(json.JSONDecodeError):
                win_backend._run_powershell_json("Get-Disk")

        # 3. _lock_and_dismount_disk_volumes closes handles if second volume fails to lock
        opened: list[int] = []
        closed: list[int] = []

        class _FakeK32:
            def CreateFileW(self, *_a):
                h = 100 + len(opened)
                opened.append(h)
                return h

            def DeviceIoControl(self, h, code, *_a):
                if h == 100:
                    return 1
                return 0  # Fail lock on 2nd handle across retries

            def CloseHandle(self, h):
                closed.append(h)

        fake_windll = mock.MagicMock()
        fake_windll.kernel32 = _FakeK32()
        with (
            mock.patch("engine.win_backend._get_disk_partitions_win", return_value=[{"DriveLetter": "E"}, {"DriveLetter": "F"}]),
            mock.patch.object(win_backend.ctypes, "windll", fake_windll, create=True),
            mock.patch.object(win_backend.ctypes, "GetLastError", return_value=5, create=True),
            mock.patch("time.sleep"),
        ):
            with self.assertRaises(RuntimeError):
                win_backend._lock_and_dismount_disk_volumes(2)
        self.assertEqual(sorted(closed), [100, 101])

        # 4. Simulated Win32 raw write capacity overflow + simulated destructive badblocks mismatch
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            fake_disk = tmp_p / "phys_disk.bin"
            # 16 KiB disk image with corrupted pattern at offset 4096
            fake_disk.write_bytes(b"\xAA\x55" * 2048 + b"\xFF\x00" * 2048 + b"\xAA\x55" * 4096)

            class _FakeWriteK32:
                def CreateFileW(self, *_a):
                    return 777

                def DeviceIoControl(self, *_a):
                    return 1

                def SetFilePointerEx(self, *_a):
                    return 1

                def WriteFile(self, *_a):
                    return 1

                def FlushFileBuffers(self, *_a):
                    return 1

                def CloseHandle(self, *_a):
                    return 1

            fake_windll_w = mock.MagicMock()
            fake_windll_w.kernel32 = _FakeWriteK32()
            small_usb = _make_usb_dev(str(fake_disk), size_bytes=1024)

            gz_big = tmp_p / "big.img.gz"
            with gzip.open(gz_big, "wb") as gf:
                gf.write(b"Z" * 4096)

            with (
                mock.patch("engine.win_backend._lock_and_dismount_disk_volumes", return_value=[]),
                mock.patch("engine.win_backend.validate_windows_target", return_value=(small_usb, 2)),
                mock.patch("engine.win_backend._run_diskpart"),
                mock.patch("engine.win_backend.is_windows_admin", return_value=True),
                mock.patch.object(win_backend.ctypes, "windll", fake_windll_w, create=True),
            ):
                with self.assertRaises(RuntimeError):
                    win_backend.execute_win_toolbox(
                        {"action": "usb_restore_img", "device": str(fake_disk), "image_path": str(gz_big)},
                        dry_run=False,
                        emit=lambda *a, **kw: None,
                        emit_progress=lambda *a, **kw: None,
                        emit_log=lambda *a, **kw: None,
                    )

            # 5. Simulated Win32 usb_badblocks (destructive=True) detects corrupted 0xAA55 sectors
            bad_usb = _make_usb_dev(str(fake_disk), size_bytes=16384)
            bb_events: list[dict] = []
            with (
                mock.patch("engine.win_backend._lock_and_dismount_disk_volumes", return_value=[]),
                mock.patch("engine.win_backend.validate_windows_target", return_value=(bad_usb, 2)),
                mock.patch("engine.win_backend._run_diskpart"),
                mock.patch("engine.win_backend.is_windows_admin", return_value=True),
                mock.patch.object(win_backend.ctypes, "windll", fake_windll_w, create=True),
            ):
                win_backend.execute_win_toolbox(
                    {"action": "usb_badblocks", "device": str(fake_disk), "destructive": True},
                    dry_run=False,
                    emit=lambda et, **kw: bb_events.append({"type": et, **kw}),
                    emit_progress=lambda *a, **kw: None,
                    emit_log=lambda *a, **kw: None,
                )
            bb_res = [e for e in bb_events if e.get("type") == "badblocks_result"]
            self.assertEqual(len(bb_res), 1)
            self.assertGreaterEqual(bb_res[0]["bad_blocks"], 1)


if __name__ == "__main__":
    unittest.main()
