import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from engine.disk_utils import (
    SafetyViolationError,
    parse_lsblk_json,
    plan_partition_layout,
    sanitize_volume_label,
    validate_target_device,
)
from engine.iso_inspector import compute_hashes
from engine.unattend_builder import build_autounattend_xml, validate_windows_username
from engine.ventoy_manager import build_ventoy_json, normalize_ventoy_image_path


SAMPLE_LSBLK = json.dumps({
    "blockdevices": [
        {
            "name": "/dev/nvme0n1",
            "kname": "/dev/nvme0n1",
            "size": 512110190592,
            "model": "Samsung SSD 980",
            "vendor": None,
            "serial": "S123",
            "tran": "nvme",
            "rm": False,
            "ro": False,
            "hotplug": False,
            "type": "disk",
            "pttype": "gpt",
            "children": [
                {"kname": "nvme0n1p1", "path": "/dev/nvme0n1p1", "size": 104857600, "fstype": "vfat", "mountpoints": ["/boot/efi"]},
                {"kname": "nvme0n1p7", "path": "/dev/nvme0n1p7", "size": 66000000000, "fstype": "ext4", "mountpoints": ["/"]},
            ],
        },
        {
            "name": "/dev/sdb",
            "kname": "/dev/sdb",
            "size": 31914983424,
            "model": "hp v222w",
            "vendor": "hp",
            "serial": "U456",
            "tran": "usb",
            "rm": True,
            "ro": False,
            "hotplug": True,
            "type": "disk",
            "pttype": "gpt",
            "children": [
                {"kname": "sdb1", "path": "/dev/sdb1", "size": 31800000000, "fstype": "exfat", "label": "Ventoy", "mountpoints": ["/run/media/user/Ventoy"]},
                {"kname": "sdb2", "path": "/dev/sdb2", "size": 33554432, "fstype": "vfat", "label": "VTOYEFI", "mountpoints": []},
            ],
        },
    ]
})


class BootForgeEngineTests(unittest.TestCase):
    def test_system_disk_protection(self):
        devices = parse_lsblk_json(SAMPLE_LSBLK, collect_sysfs=False)
        self.assertEqual(len(devices), 2)
        nvme = next(d for d in devices if d.name == "nvme0n1")
        usb = next(d for d in devices if d.name == "sdb")

        self.assertTrue(nvme.is_system_disk)
        self.assertFalse(usb.is_system_disk)
        self.assertTrue(usb.ventoy_detected)

        # Must block OS disk, partition path, and arbitrary paths
        with self.assertRaises(SafetyViolationError):
            validate_target_device("/dev/nvme0n1", devices=devices)
        with self.assertRaises(SafetyViolationError):
            validate_target_device("/dev/sdb1", devices=devices)
        with self.assertRaises(SafetyViolationError):
            validate_target_device("/dev/sdb; rm -rf /", devices=devices)

        # Safe USB passes validation
        validated = validate_target_device("/dev/sdb", devices=devices)
        self.assertEqual(validated.path, "/dev/sdb")

    def test_volume_label_sanitization(self):
        self.assertEqual(sanitize_volume_label("Win 11 Fr & Test!", "fat32"), "WIN_11_FR__")
        self.assertEqual(len(sanitize_volume_label("ABCDEFGHIJKLMNOP", "fat32")), 11)
        self.assertEqual(sanitize_volume_label("Windows 11 25H2", "ntfs"), "Windows 11 25H2")
        self.assertEqual(sanitize_volume_label("!!!", "fat32"), "___")

    def test_partition_layout_blueprints(self):
        size_32g = 32 * 1024**3
        rufus_win = plan_partition_layout(size_32g, "direct", scheme="gpt", filesystem="ntfs", uefi_ntfs=True)
        self.assertEqual([s["id"] for s in rufus_win], ["main-payload", "uefi-ntfs"])

        rufus_linux = plan_partition_layout(size_32g, "direct", scheme="gpt", filesystem="fat32", persistence_gb=6.0)
        self.assertEqual([s["id"] for s in rufus_linux], ["main-payload", "linux-persistence"])
        self.assertEqual(rufus_linux[1]["size_mib"], 6 * 1024)

        ventoy_plan = plan_partition_layout(size_32g, "ventoy", filesystem="exfat", ventoy_reserve_mb=1024)
        self.assertEqual([s["id"] for s in ventoy_plan], ["ventoy-data", "ventoy-efi", "ventoy-reserved"])

    def test_autounattend_xml_escaping_and_structure(self):
        xml_str = build_autounattend_xml({
            "bypass_tpm_secureboot_ram": True,
            "bypass_microsoft_account": True,
            "disable_bitlocker": True,
            "disable_telemetry": True,
            "disable_consumer_bloat": True,
            "create_local_account": True,
            "local_username": "Farel_Admin",
            "local_password": "P@ss<&>\"'123",
            "locale": "fr-FR",
            "timezone": "Romance Standard Time",
        })
        root = ET.fromstring(xml_str)
        self.assertIn("unattend", root.tag)
        self.assertIn("BypassTPMCheck", xml_str)
        self.assertIn("BypassNRO", xml_str)
        self.assertIn("PreventDeviceEncryption", xml_str)
        self.assertIn("Farel_Admin", xml_str)
        self.assertIn("P@ss&lt;&amp;&gt;\"'123", xml_str)

        with self.assertRaises(ValueError):
            validate_windows_username("CON")
        with self.assertRaises(ValueError):
            validate_windows_username("bad user!")

    def test_ventoy_json_builder_and_path_validation(self):
        doc = build_ventoy_json({
            "win11_bypass_check": True,
            "win11_bypass_nro": True,
            "tree_view": True,
            "menu_timeout": 15,
            "default_image": "Win11_25H2.iso",
            "menu_alias": [{"image": "/Win11_25H2.iso", "alias": "Windows 11 25H2 (Rufus Tweaks)"}],
            "auto_install": [{"image": "/Win11_25H2.iso", "template": "/ventoy/scripts/win11.xml"}],
            "persistence": [{"image": "/kali-live.iso", "backend": "/ventoy/persistence/kali.dat"}],
        })
        self.assertEqual(doc["auto_install"][0]["template"], "/ventoy/scripts/win11.xml")
        self.assertEqual(doc["persistence"][0]["backend"], "/ventoy/persistence/kali.dat")

        with self.assertRaises(ValueError):
            normalize_ventoy_image_path("../../etc/passwd")

    def test_streaming_hash_verifier(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
            tmp.write(b"BootForge-Verify-Data-Block")
            tmp_path = tmp.name
        try:
            res1 = compute_hashes(tmp_path, algorithms=["sha256", "md5"])
            sha = res1["hashes"]["sha256"]
            res2 = compute_hashes(tmp_path, algorithms=["sha256"], expected_hash=sha.upper())
            self.assertTrue(res2["matched"])
            res3 = compute_hashes(tmp_path, algorithms=["sha256"], expected_hash="0" * 64)
            self.assertFalse(res3["matched"])
        finally:
            os.unlink(tmp_path)

    def test_cli_preview_subcommands(self):
        engine = str(Path(__file__).resolve().parent.parent / "bootforge_engine.py")
        p_xml = subprocess.run(
            [
                sys.executable,
                engine,
                "preview-unattend",
                "--config",
                json.dumps({
                    "bypass_tpm_secureboot_ram": True,
                    "create_local_account": True,
                    "local_username": "StudioUser",
                }),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        ET.fromstring(p_xml.stdout)
        self.assertIn("StudioUser", p_xml.stdout)

        p_vjson = subprocess.run(
            [
                sys.executable,
                engine,
                "preview-ventoy-json",
                "--config",
                json.dumps({
                    "win11_bypass_check": True,
                    "tree_view": True,
                    "menu_timeout": 12,
                }),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        doc = json.loads(p_vjson.stdout)
        self.assertIn("control", doc)
        self.assertTrue(any(c.get("VTOY_MENU_TIMEOUT") == "12" for c in doc["control"]))

    def test_cli_dry_run_safety_lock_on_system_disk(self):
        engine = str(Path(__file__).resolve().parent.parent / "bootforge_engine.py")
        lsblk_proc = subprocess.run(
            ["lsblk", "-J", "-b", "-p", "-o", "NAME,KNAME,SIZE,MODEL,VENDOR,SERIAL,TRAN,RM,RO,HOTPLUG,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MOUNTPOINTS,PTTYPE"],
            capture_output=True,
            text=True,
            check=True,
        )
        live_devs = parse_lsblk_json(lsblk_proc.stdout, collect_sysfs=False)
        sys_disks = [d for d in live_devs if d.is_system_disk]
        if not sys_disks:
            self.skipTest("Aucun disque système trouvé dans lsblk")

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as jf:
            json.dump({"action": "ventoy_install", "device": sys_disks[0].path}, jf)
            job_path = jf.name
        try:
            res = subprocess.run(
                [sys.executable, engine, "run-job", "--job", job_path, "--dry-run"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 2)
            self.assertIn("SAFETY_BLOCKED", res.stdout)
        finally:
            os.unlink(job_path)


if __name__ == "__main__":
    unittest.main()
