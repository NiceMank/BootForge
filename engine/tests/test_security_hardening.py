"""Targeted security hardening & regression tests for BootForge engine and backends."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from engine import bootforge_engine, net_security, ventoy_manager, win_backend
from engine.disk_utils import (
    BlockDeviceInfo,
    PartitionInfo,
    SafetyViolationError,
    is_critical_mountpoint,
    parse_lsblk_json,
    validate_paths_not_on_device,
    validate_target_device,
)


def _make_device(**overrides) -> BlockDeviceInfo:
    defaults = {
        "name": "sdb",
        "path": "/dev/sdb",
        "size_bytes": 16 * 1024**3,
        "model": "USB",
        "vendor": "Vendor",
        "serial": "123",
        "transport": "usb",
        "removable": True,
        "read_only": False,
        "hotplug": True,
        "pttype": "gpt",
        "usb_speed_mbps": 480,
        "vid_pid": "346d:5678",
        "is_system_disk": False,
        "system_reasons": [],
        "ventoy_detected": False,
        "partitions": [],
    }
    defaults.update(overrides)
    return BlockDeviceInfo(**defaults)


class SecurityHardeningTests(unittest.TestCase):
    def test_critical_mountpoints_and_subtrees(self) -> None:
        self.assertTrue(is_critical_mountpoint("/"))
        self.assertTrue(is_critical_mountpoint("/boot/efi"))
        self.assertTrue(is_critical_mountpoint("/var/lib/docker"))
        self.assertTrue(is_critical_mountpoint("/usr/local"))
        self.assertTrue(is_critical_mountpoint("[SWAP]"))
        self.assertFalse(is_critical_mountpoint("/run/media/niceman/USB"))
        self.assertFalse(is_critical_mountpoint("/media/USB"))

    def test_parse_lsblk_nested_luks_lvm_blocks_system_disk(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "name": "/dev/sdc",
                    "kname": "sdc",
                    "size": 64000000000,
                    "model": "External SSD",
                    "vendor": "SanDisk",
                    "serial": "EXT999",
                    "tran": "usb",
                    "rm": True,
                    "ro": False,
                    "hotplug": True,
                    "type": "disk",
                    "children": [
                        {
                            "name": "/dev/sdc1",
                            "kname": "sdc1",
                            "size": 64000000000,
                            "type": "part",
                            "mountpoints": [],
                            "children": [
                                {
                                    "name": "/dev/mapper/crypt_os",
                                    "kname": "dm-0",
                                    "type": "crypt",
                                    "mountpoints": [],
                                    "children": [
                                        {
                                            "name": "/dev/mapper/vg-var",
                                            "kname": "dm-1",
                                            "type": "lvm",
                                            "mountpoints": ["/var/log"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        devs = parse_lsblk_json(json.dumps(payload), collect_sysfs=False)
        self.assertEqual(len(devs), 1)
        self.assertTrue(devs[0].is_system_disk)
        with self.assertRaises(SafetyViolationError):
            validate_target_device("/dev/sdc", devices=devs)

    def test_validate_target_device_resolves_symlink_trap(self) -> None:
        usb = _make_device()
        os_nvme = _make_device(
            name="nvme0n1",
            path="/dev/nvme0n1",
            size_bytes=512 * 1024**3,
            model="NVMe OS",
            vendor="",
            serial="OS1",
            transport="nvme",
            removable=False,
            hotplug=False,
            usb_speed_mbps=None,
            vid_pid=None,
            is_system_disk=True,
            system_reasons=["Partition système active (/)"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake_link = Path(tmp) / "sneaky-link"
            os.symlink("/dev/nvme0n1", fake_link)
            with self.assertRaises(SafetyViolationError):
                validate_target_device(str(fake_link), devices=[usb, os_nvme])

            part_link = Path(tmp) / "part-link"
            os.symlink("/dev/sdb1", part_link)
            with self.assertRaises(SafetyViolationError):
                validate_target_device(str(part_link), devices=[usb, os_nvme])

    def test_validate_paths_not_on_device_blocks_target_mount_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usb_mnt = root / "KALI LINUX"
            other_mnt = root / "KALI LINUX_BACKUP"
            usb_mnt.mkdir()
            other_mnt.mkdir()
            iso_on_usb = usb_mnt / "test.iso"
            iso_on_other = other_mnt / "test.iso"
            iso_on_usb.write_bytes(b"x")
            iso_on_other.write_bytes(b"x")

            dev = _make_device(
                vendor="",
                serial="",
                partitions=[
                    PartitionInfo(
                        name="sdb1",
                        path="/dev/sdb1",
                        size_bytes=16 * 1024**3,
                        fstype="vfat",
                        label="KALI LINUX",
                        uuid="",
                        mountpoints=[str(usb_mnt)],
                    )
                ],
            )
            with self.assertRaises(SafetyViolationError):
                validate_paths_not_on_device(dev, str(iso_on_usb))
            # Sibling prefix directory must NOT be blocked
            validate_paths_not_on_device(dev, str(iso_on_other))

    def test_ventoy_sha256_fail_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            ventoy_manager._fetch_required_ventoy_sha256(None, "ventoy-1.0.99-linux.tar.gz")

        with mock.patch("engine.ventoy_manager.fetch_https_bytes", return_value=b"0" * 64 + b"  other-file.tar.gz\n"):
            with self.assertRaises(RuntimeError):
                ventoy_manager._fetch_required_ventoy_sha256(
                    {"browser_download_url": "https://github.com/ventoy/Ventoy/releases/download/v1.0.99/sha256.txt"},
                    "ventoy-1.0.99-linux.tar.gz",
                )

    def test_ventoy_safe_extract_zip_and_tar_rejects_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            dest = tmp_p / "extract"
            dest.mkdir()

            # 1. Zip traversal
            bad_zip = tmp_p / "bad_trav.zip"
            with zipfile.ZipFile(bad_zip, "w") as zf:
                zf.writestr("../../outside.txt", "pwn")
            with self.assertRaises(ValueError):
                ventoy_manager.safe_extract_zip(bad_zip, dest)

            # 2. Zip symlink
            sym_zip = tmp_p / "bad_sym.zip"
            with zipfile.ZipFile(sym_zip, "w") as zf:
                zi = zipfile.ZipInfo("link.txt")
                zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(zi, "/etc/passwd")
            with self.assertRaises(ValueError):
                ventoy_manager.safe_extract_zip(sym_zip, dest)

            # 3. Tar symlink & sibling prefix trap
            sym_tar = tmp_p / "bad_sym.tar.gz"
            with tarfile.open(sym_tar, "w:gz") as tf:
                ti = tarfile.TarInfo("evil_link")
                ti.type = tarfile.SYMTYPE
                ti.linkname = "/etc/shadow"
                tf.addfile(ti)
            with self.assertRaises(ValueError):
                ventoy_manager.safe_extract_tar(sym_tar, dest)

            sibling_tar = tmp_p / "sibling.tar.gz"
            with tarfile.open(sibling_tar, "w:gz") as tf:
                ti = tarfile.TarInfo("../extract_evil/pwn.txt")
                data = b"pwn"
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            with self.assertRaises(ValueError):
                ventoy_manager.safe_extract_tar(sibling_tar, dest)

            # 4. Safe tar extracts cleanly
            good_tar = tmp_p / "good.tar.gz"
            with tarfile.open(good_tar, "w:gz") as tf:
                ti = tarfile.TarInfo("ventoy-1.0.99/README.txt")
                data = b"ok"
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            ventoy_manager.safe_extract_tar(good_tar, dest)
            self.assertEqual((dest / "ventoy-1.0.99" / "README.txt").read_bytes(), b"ok")

    def test_net_security_rejects_http_credentials_and_downgrade_redirect(self) -> None:
        with self.assertRaises(ValueError):
            net_security.validate_https_url("http://github.com/a/b")
        with self.assertRaises(ValueError):
            net_security.validate_https_url("https://user:pass@github.com/a/b")
        with self.assertRaises(ValueError):
            net_security.validate_https_url("https://evil.example.org/payload.bin", allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS)

        redir = net_security._StrictHttpsRedirectHandler(allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS)
        req = urllib.request.Request("https://github.com/a/b")
        with self.assertRaises(ValueError):
            redir.redirect_request(req, None, 302, "Found", {}, "http://github.com/downgrade")
        with self.assertRaises(ValueError):
            redir.redirect_request(req, None, 302, "Found", {}, "https://evil.com/hijack")

    def test_net_security_atomic_download_sha_mismatch_cleans_part_file(self) -> None:
        payload = b"bootforge-test-payload"
        good_sha = hashlib.sha256(payload).hexdigest()
        bad_sha = "0" * 64

        class _FakeResp:
            def __init__(self) -> None:
                self._bio = io.BytesIO(payload)
                self.headers = {"Content-Length": str(len(payload))}

            def geturl(self) -> str:
                return "https://objects.githubusercontent.com/test.bin"

            def read(self, n: int = -1) -> bytes:
                return self._bio.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeOpener:
            def open(self, req, timeout=45):
                return _FakeResp()

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.bin"
            with mock.patch("engine.net_security.build_https_opener", return_value=_FakeOpener()):
                with self.assertRaises(ValueError):
                    net_security.download_https_atomic(
                        "https://github.com/NiceMank/BootForge/releases/download/v1.0.1/test.bin",
                        dest,
                        allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS,
                        expected_sha256=bad_sha,
                    )
                self.assertFalse(dest.exists())
                self.assertEqual(list(Path(tmp).glob("*.part")), [])

                # Valid SHA succeeds atomically
                out = net_security.download_https_atomic(
                    "https://github.com/NiceMank/BootForge/releases/download/v1.0.1/test.bin",
                    dest,
                    allowed_hosts=net_security.GITHUB_ALLOWED_HOSTS,
                    expected_sha256=good_sha,
                )
                self.assertEqual(out.read_bytes(), payload)
                self.assertEqual(list(Path(tmp).glob("*.part")), [])

    def test_usb_restore_img_dry_run_linux_and_windows(self) -> None:
        fake_usb = _make_device()
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "backup.img"
            img.write_bytes(b"IMG" * 1024)
            events: list[dict] = []
            with (
                mock.patch("sys.platform", "linux"),
                mock.patch("engine.bootforge_engine.validate_target_device", return_value=fake_usb),
                mock.patch("engine.bootforge_engine.emit", side_effect=lambda et, **kw: events.append({"type": et, **kw})),
            ):
                bootforge_engine.execute_job_file(
                    self._write_job(tmp, {"action": "usb_restore_img", "device": "/dev/sdb", "image_path": str(img)}),
                    dry_run=True,
                )
            self.assertTrue(any(e.get("type") == "dry_run_plan" and e.get("mode") == "usb_restore_img" for e in events))

            # Windows dry-run restore
            win_usb = _make_device(
                name="PhysicalDrive2",
                path=r"\\.\PhysicalDrive2",
                vendor="",
                serial="",
                usb_speed_mbps=None,
                vid_pid=None,
            )
            win_events: list[dict] = []
            with mock.patch("engine.win_backend.validate_windows_target", return_value=(win_usb, 2)):
                win_backend.execute_win_toolbox(
                    {"action": "usb_restore_img", "device": r"\\.\PhysicalDrive2", "image_path": str(img)},
                    dry_run=True,
                    emit=lambda et, **kw: win_events.append({"type": et, **kw}),
                    emit_progress=lambda *a: None,
                    emit_log=lambda *a: None,
                )
            self.assertTrue(any(e.get("type") == "dry_run_plan" and e.get("mode") == "usb_restore_img" for e in win_events))

    def test_win_powershell_uses_env_var_for_iso_path(self) -> None:
        captured: dict = {}

        def _fake_ps(script: str, timeout: int = 15, extra_env=None):
            captured["script"] = script
            captured["env"] = extra_env
            return []

        evil_iso = r"C:\Users\Test'; Start-Process calc; '\win11.iso"
        with (
            mock.patch("engine.win_backend._find_7z_windows", return_value=None),
            mock.patch("engine.win_backend._run_powershell_json", side_effect=_fake_ps),
        ):
            win_backend.inspect_iso_windows_mounted(evil_iso)
        self.assertEqual(captured["env"], {"BF_ISO_PATH": evil_iso})
        self.assertIn("$env:BF_ISO_PATH", captured["script"])
        self.assertNotIn("Start-Process", captured["script"])

    @staticmethod
    def _write_job(tmp_dir: str, payload: dict) -> str:
        p = Path(tmp_dir) / "job.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)
