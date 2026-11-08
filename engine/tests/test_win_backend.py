"""Unit tests for BootForge Windows backend and disk management."""

from __future__ import annotations

import io
import json
import os
import unittest
from pathlib import Path

from engine.disk_utils import BlockDeviceInfo, PartitionInfo, SafetyViolationError
from engine.iso_inspector import _inspect_iso9660_python
from engine.ventoy_manager import build_ventoy_windows_cli_args
from engine.win_backend import (
    build_diskpart_flash_script,
    check_windows_dependencies,
    parse_windows_disks_json,
    validate_windows_target,
)

SAMPLE_PS_DISKS = json.dumps([
    {
        "Number": 0,
        "FriendlyName": "NVMe Samsung 980 PRO 1TB",
        "SerialNumber": "S6B0NS0R123456",
        "Size": 1000204886016,
        "BusType": "NVMe",
        "MediaType": "SSD",
        "IsReadOnly": False,
        "IsSystem": True,
        "IsBoot": True,
        "PartitionStyle": "GPT",
        "Partitions": [
            {"PartitionNumber": 1, "Offset": 1048576, "Size": 104857600, "DriveLetter": "", "Label": "System", "FileSystem": "FAT32"},
            {"PartitionNumber": 3, "Offset": 122683392, "Size": 999000000000, "DriveLetter": "C", "Label": "Windows", "FileSystem": "NTFS"},
        ],
    },
    {
        "Number": 1,
        "FriendlyName": "SanDisk Ultra USB 3.0",
        "SerialNumber": "4C53000123456789",
        "Size": 31914983424,
        "BusType": "USB",
        "MediaType": "Removable Media",
        "IsReadOnly": False,
        "IsSystem": False,
        "IsBoot": False,
        "PartitionStyle": "GPT",
        "Partitions": [
            {"PartitionNumber": 1, "Offset": 1048576, "Size": 31800000000, "DriveLetter": "E", "Label": "Ventoy", "FileSystem": "exFAT"},
            {"PartitionNumber": 2, "Offset": 31801048576, "Size": 33554432, "DriveLetter": "", "Label": "VTOYEFI", "FileSystem": "FAT16"},
        ],
    },
])


class WindowsBackendTests(unittest.TestCase):
    def test_parse_windows_disks_inventory(self):
        devices = parse_windows_disks_json(SAMPLE_PS_DISKS, system_drive_letter="C:")
        self.assertEqual(len(devices), 2)

        sys_disk = devices[0]
        usb_disk = devices[1]

        self.assertEqual(sys_disk.name, "PhysicalDrive0")
        self.assertTrue(sys_disk.is_system_disk)
        self.assertIn("Disque principal système (Disque 0)", sys_disk.system_reasons)
        self.assertIn("Contient le lecteur système (C:)", sys_disk.system_reasons)

        self.assertEqual(usb_disk.name, "PhysicalDrive1")
        self.assertEqual(usb_disk.path, r"\\.\PhysicalDrive1")
        self.assertFalse(usb_disk.is_system_disk)
        self.assertTrue(usb_disk.removable)
        self.assertTrue(usb_disk.ventoy_detected)
        self.assertEqual(len(usb_disk.partitions), 2)
        self.assertEqual(usb_disk.partitions[0].mountpoints, ["E:\\"])

    def test_validate_windows_target_safety_rules(self):
        devices = parse_windows_disks_json(SAMPLE_PS_DISKS, system_drive_letter="C:")

        # Reject PhysicalDrive0 (System Disk 0)
        with self.assertRaises(SafetyViolationError):
            validate_windows_target(r"\\.\PhysicalDrive0", devices=devices)

        # Reject non PhysicalDrive syntax (e.g. partition or drive letter)
        with self.assertRaises(SafetyViolationError):
            validate_windows_target("C:\\", devices=devices)
        with self.assertRaises(SafetyViolationError):
            validate_windows_target(r"\\.\PhysicalDrive1#Part1", devices=devices)
        with self.assertRaises(SafetyViolationError):
            validate_windows_target("PhysicalDrive1", devices=devices)

        # Valid target passes
        matched, disk_num = validate_windows_target(r"\\.\PhysicalDrive1", devices=devices)
        self.assertEqual(disk_num, 1)
        self.assertEqual(matched.path, r"\\.\PhysicalDrive1")

    def test_build_diskpart_flash_script(self):
        size_32g = 32 * 1024**3
        cmds_uefi_ntfs = build_diskpart_flash_script(1, "gpt", "ntfs", "WIN11_USB", size_32g, uefi_ntfs_boot=True)
        self.assertEqual(cmds_uefi_ntfs[0], "select disk 1")
        self.assertEqual(cmds_uefi_ntfs[1], "clean")
        self.assertEqual(cmds_uefi_ntfs[2], "convert gpt")
        self.assertTrue(any("format fs=ntfs quick label=\"WIN11_USB\"" in c for c in cmds_uefi_ntfs))
        self.assertTrue(any("format fs=fat quick label=\"UEFI_NTFS\"" in c for c in cmds_uefi_ntfs))
        self.assertEqual(cmds_uefi_ntfs[-1], "exit")

        cmds_fat32 = build_diskpart_flash_script(2, "mbr", "fat32", "BOOT_DRIVE", size_32g, uefi_ntfs_boot=False)
        self.assertEqual(cmds_fat32[0], "select disk 2")
        self.assertEqual(cmds_fat32[2], "convert mbr")
        self.assertTrue(any("format fs=fat32 quick label=\"BOOT_DRIVE\"" in c for c in cmds_fat32))

    def test_build_ventoy_windows_cli_args(self):
        # Install with GPT and SecureBoot
        args1 = build_ventoy_windows_cli_args("ventoy_install", 1, "gpt", secure_boot=True, reserve_mb=0)
        self.assertIn("VTOYCLI", args1)
        self.assertIn("/I", args1)
        self.assertIn("/PhyDrive:1", args1)
        self.assertIn("/GPT", args1)
        self.assertNotIn("/NOSB", args1)

        # Install without Secure Boot and with reserved space
        args2 = build_ventoy_windows_cli_args("ventoy_install", 2, "mbr", secure_boot=False, reserve_mb=2048)
        self.assertIn("/I", args2)
        self.assertIn("/PhyDrive:2", args2)
        self.assertNotIn("/GPT", args2)
        self.assertIn("/NOSB", args2)
        self.assertIn("/R:2048", args2)

        # Update
        args3 = build_ventoy_windows_cli_args("ventoy_update", 1, "gpt", secure_boot=True, reserve_mb=0)
        self.assertIn("/U", args3)
        self.assertIn("/PhyDrive:1", args3)

    def test_synthetic_iso9660_pure_python_inspector(self):
        # Create a tiny 64 KiB mock ISO with a Primary Volume Descriptor at sector 16 (offset 0x8000)
        data = bytearray(64 * 1024)
        pvd_offset = 16 * 2048
        # Standard ISO9660 Volume Descriptor: byte 0 = type (1 = PVD), bytes 1-5 = 'CD001', byte 6 = version (1)
        data[pvd_offset : pvd_offset + 7] = b"\x01CD001\x01"
        # Volume ID at offset 40 (32 chars)
        vol_id = b"BOOTFORGE_TEST".ljust(32, b" ")
        data[pvd_offset + 40 : pvd_offset + 72] = vol_id
        # Volume space size (little-endian uint32 at offset 80)
        data[pvd_offset + 80 : pvd_offset + 84] = (32).to_bytes(4, "little")

        import tempfile
        with tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            entries = _inspect_iso9660_python(tmp_path)
            self.assertIsInstance(entries, list)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
