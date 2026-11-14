"""Comprehensive cryptographic, security, and platform tests for BootForge auto-updater."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from engine import updater

_SIGN_RELEASE_PATH = Path(__file__).resolve().parents[2] / "packaging" / "sign_release.py"
_spec = importlib.util.spec_from_file_location("bootforge_sign_release", _SIGN_RELEASE_PATH)
assert _spec is not None and _spec.loader is not None
sign_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sign_release)


def _gen_keypair() -> tuple[Ed25519PrivateKey, bytes, bytes]:
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, priv_raw, pub_raw


class UpdaterTests(unittest.TestCase):
    def test_pure_python_ed25519_matches_cryptography_and_rejects_forgeries(self) -> None:
        priv, _priv_raw, pub = _gen_keypair()
        for msg in (b"", b"BootForge manifest v1.0.2", os_bytes := bytes(range(256))):
            sig = priv.sign(msg)
            self.assertTrue(updater.ed25519_verify(pub, msg, sig))
            # Flip 1 bit in signature
            bad_sig = bytearray(sig)
            bad_sig[10] ^= 0x01
            self.assertFalse(updater.ed25519_verify(pub, msg, bytes(bad_sig)))
            # Flip 1 bit in message
            self.assertFalse(updater.ed25519_verify(pub, msg + b"\x00", sig))
            # Wrong key
            _, _, pub2 = _gen_keypair()
            self.assertFalse(updater.ed25519_verify(pub2, msg, sig))

        # Invalid S >= L rejected cleanly
        bad_s = bytearray(priv.sign(os_bytes))
        bad_s[32:] = (updater._L + 1).to_bytes(32, "little")
        self.assertFalse(updater.ed25519_verify(pub, os_bytes, bytes(bad_s)))
        # Wrong lengths & identity point rejected
        self.assertFalse(updater.ed25519_verify(b"\x01" * 31, b"x", b"\x00" * 64))
        self.assertFalse(updater.ed25519_verify(b"\x01" + b"\x00" * 31, b"x", b"\x00" * 64))

    def test_semver_precedence_and_anti_downgrade(self) -> None:
        self.assertLess(updater.SemVer.parse("v1.0.1"), updater.SemVer.parse("v1.0.2"))
        self.assertLess(updater.SemVer.parse("1.0.2-rc.1"), updater.SemVer.parse("1.0.2"))
        self.assertLess(updater.SemVer.parse("1.0.2-alpha.1"), updater.SemVer.parse("1.0.2-beta.2"))
        with self.assertRaises(ValueError):
            updater.SemVer.parse("latest")

    def test_verify_manifest_fail_closed_cases(self) -> None:
        priv, _, pub = _gen_keypair()
        manifest = {
            "schema_version": 1,
            "product": "BootForge",
            "repo": "NiceMank/BootForge",
            "key_id": "bootforge-release-2026",
            "version": "1.0.2",
            "tag": "v1.0.2",
            "assets": {},
        }
        m_bytes = json.dumps(manifest).encode("utf-8")
        sig_b64 = base64.b64encode(priv.sign(m_bytes)).decode("ascii").encode("ascii")

        # 1. Default unprovisioned key must refuse
        with self.assertRaises(updater.UpdateVerificationError) as ctx:
            updater.verify_manifest_bytes(m_bytes, sig_b64, trusted_keys={"bootforge-release-2026": ""})
        self.assertIn("UNPROVISIONED_KEY", str(ctx.exception))

        # 2. Valid key verifies cleanly
        parsed = updater.verify_manifest_bytes(
            m_bytes,
            sig_b64,
            trusted_keys={"bootforge-release-2026": pub.hex()},
        )
        self.assertEqual(parsed["version"], "1.0.2")

        # 3. Tampered manifest fails Ed25519 check
        tampered = json.dumps({**manifest, "version": "9.9.9"}).encode("utf-8")
        with self.assertRaises(updater.UpdateVerificationError) as ctx2:
            updater.verify_manifest_bytes(
                tampered,
                sig_b64,
                trusted_keys={"bootforge-release-2026": pub.hex()},
            )
        self.assertIn("INVALID_SIGNATURE", str(ctx2.exception))

        # 4. Repo mismatch rejected even if signed by that key
        evil_repo = json.dumps({**manifest, "repo": "Attacker/BootForge"}).encode("utf-8")
        evil_sig = base64.b64encode(priv.sign(evil_repo)).decode("ascii").encode("ascii")
        with self.assertRaises(updater.UpdateVerificationError):
            updater.verify_manifest_bytes(
                evil_repo,
                evil_sig,
                trusted_keys={"bootforge-release-2026": pub.hex()},
            )

    def test_sign_release_roundtrip_and_check_for_update_all_states(self) -> None:
        priv, priv_raw, pub = _gen_keypair()
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            deb_file = dist / "bootforge_1.2.0_all.deb"
            deb_file.write_bytes(b"FAKE-DEB-PAYLOAD")
            win_setup = dist / "BootForge-1.2.0-win64-setup.exe"
            win_setup.write_bytes(b"FAKE-WIN-EXE")

            rc = sign_release.main(
                [
                    "--dist-dir",
                    str(dist),
                    "--tag",
                    "v1.2.0",
                    "--signing-key",
                    base64.b64encode(priv_raw).decode("ascii"),
                ]
            )
            self.assertEqual(rc, 0)
            m_bytes = (dist / "BOOTFORGE_MANIFEST.json").read_bytes()
            sig_bytes = (dist / "BOOTFORGE_MANIFEST.json.sig").read_bytes()

            rel_json = json.dumps(
                {
                    "tag_name": "v1.2.0",
                    "html_url": "https://github.com/NiceMank/BootForge/releases/tag/v1.2.0",
                    "body": "## Changelog\n- Hardened updater",
                    "published_at": "2026-11-13T16:00:00Z",
                    "assets": [
                        {
                            "name": "BOOTFORGE_MANIFEST.json",
                            "browser_download_url": "https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BOOTFORGE_MANIFEST.json",
                        },
                        {
                            "name": "BOOTFORGE_MANIFEST.json.sig",
                            "browser_download_url": "https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BOOTFORGE_MANIFEST.json.sig",
                        },
                    ],
                }
            ).encode("utf-8")

            def _fake_fetch(url: str, **_kw) -> bytes:
                if url.endswith("/releases/latest"):
                    return rel_json
                if url.endswith("BOOTFORGE_MANIFEST.json"):
                    return m_bytes
                if url.endswith("BOOTFORGE_MANIFEST.json.sig"):
                    return sig_bytes
                raise AssertionError(f"Unexpected URL {url}")

            keys = {"bootforge-release-2026": pub.hex()}
            with mock.patch("engine.updater.fetch_https_bytes", side_effect=_fake_fetch):
                # Available on Linux deb channel
                res = updater.check_for_update(
                    current_version="1.0.1",
                    trusted_keys=keys,
                    install_channel="linux_deb",
                )
                self.assertEqual(res.status, "available")
                self.assertTrue(res.verified)
                self.assertEqual(res.asset_name, "bootforge_1.2.0_all.deb")
                self.assertEqual(res.asset_sha256, hashlib.sha256(b"FAKE-DEB-PAYLOAD").hexdigest())

                # Available on Windows setup channel
                res_win = updater.check_for_update(
                    current_version="1.0.1",
                    trusted_keys=keys,
                    install_channel="windows_setup",
                )
                self.assertEqual(res_win.status, "available")
                self.assertEqual(res_win.asset_name, "BootForge-1.2.0-win64-setup.exe")

                # Up to date / anti-downgrade when already on 1.2.0 or 2.0.0
                res_cur = updater.check_for_update(current_version="1.2.0", trusted_keys=keys)
                self.assertEqual(res_cur.status, "up_to_date")
                res_newer = updater.check_for_update(current_version="2.0.0", trusted_keys=keys)
                self.assertEqual(res_newer.status, "up_to_date")

                # Unprovisioned key fails closed
                res_unprov = updater.check_for_update(current_version="1.0.1", trusted_keys={"bootforge-release-2026": ""})
                self.assertEqual(res_unprov.status, "unprovisioned_key")
                self.assertFalse(res_unprov.verified)

    def test_download_deferral_toctou_guard_and_platform_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            # Build safe tar.gz asset
            tar_bytes_io = io.BytesIO()
            with tarfile.open(fileobj=tar_bytes_io, mode="w:gz") as tf:
                ti = tarfile.TarInfo("bootforge/README.txt")
                data = b"BootForge 1.2.0"
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            tar_bytes = tar_bytes_io.getvalue()
            tar_sha = hashlib.sha256(tar_bytes).hexdigest()

            upd_tar = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.2.0",
                tag_name="v1.2.0",
                verified=True,
                asset_kind="linux_tar",
                asset_name="BootForge-1.2.0-linux.tar.gz",
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BootForge-1.2.0-linux.tar.gz",
                asset_sha256=tar_sha,
                asset_size=len(tar_bytes),
            )

            # 1. Busy USB job defers download
            with self.assertRaises(RuntimeError):
                updater.download_verified_update(upd_tar, dest_dir=tmp_p, is_busy_fn=lambda: True)

            def _fake_dl(url, dest_path, **kw):
                Path(dest_path).write_bytes(tar_bytes)
                return Path(dest_path)

            with mock.patch("engine.updater.download_https_atomic", side_effect=_fake_dl):
                dl_path = updater.download_verified_update(upd_tar, dest_dir=tmp_p, is_busy_fn=lambda: False)
            self.assertTrue(dl_path.is_file())

            # 2. Busy USB job defers install
            with self.assertRaises(RuntimeError):
                updater.apply_downloaded_update(dl_path, upd_tar, is_busy_fn=lambda: True)

            # 3. Valid linux_tar extraction succeeds
            outcome = updater.apply_downloaded_update(dl_path, upd_tar, is_busy_fn=lambda: False)
            self.assertEqual(outcome["action"], "extracted_portable")
            self.assertEqual((Path(outcome["path"]) / "bootforge" / "README.txt").read_bytes(), b"BootForge 1.2.0")

            # 4. Anti-TOCTOU tampering before install rejected
            dl_path.write_bytes(b"TAMPERED")
            with self.assertRaises(updater.UpdateVerificationError):
                updater.apply_downloaded_update(dl_path, upd_tar, is_busy_fn=lambda: False)

            # 5. linux_deb calls pkexec runner cleanly
            deb_file = tmp_p / "bootforge_1.2.0_all.deb"
            deb_file.write_bytes(b"DEB")
            upd_deb = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.2.0",
                tag_name="v1.2.0",
                verified=True,
                asset_kind="linux_deb",
                asset_name="bootforge_1.2.0_all.deb",
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.2.0/bootforge_1.2.0_all.deb",
                asset_sha256=hashlib.sha256(b"DEB").hexdigest(),
            )
            cmds: list[list[str]] = []
            res_deb = updater.apply_downloaded_update(deb_file, upd_deb, runner=cmds.append)
            self.assertEqual(res_deb["action"], "installed_deb")
            self.assertEqual(cmds[0][0], "pkexec")
            self.assertEqual(cmds[0][-1], str(deb_file.resolve()))

            # 6. windows_portable extracts safely
            zip_file = tmp_p / "BootForge-1.2.0-win64-portable.zip"
            with zipfile.ZipFile(zip_file, "w") as zf:
                zf.writestr("BootForge/BootForge.exe", "MZ")
            zip_sha = hashlib.sha256(zip_file.read_bytes()).hexdigest()
            upd_winzip = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.2.0",
                tag_name="v1.2.0",
                verified=True,
                asset_kind="windows_portable",
                asset_name="BootForge-1.2.0-win64-portable.zip",
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BootForge-1.2.0-win64-portable.zip",
                asset_sha256=zip_sha,
            )
            res_wz = updater.apply_downloaded_update(zip_file, upd_winzip)
            self.assertEqual(res_wz["action"], "extracted_portable")
            self.assertEqual((Path(res_wz["path"]) / "BootForge" / "BootForge.exe").read_text(), "MZ")

    def test_phase4_attack_and_interruption_scenarios(self) -> None:
        self.assertTrue(updater.is_release_key_provisioned())
        priv, priv_raw, pub = _gen_keypair()
        keys = {"bootforge-release-2026": pub.hex()}

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            deb_file = dist / "bootforge_1.2.0_all.deb"
            deb_file.write_bytes(b"LEGIT-DEB-120")
            sign_release.main(
                [
                    "--dist-dir",
                    str(dist),
                    "--tag",
                    "v1.2.0",
                    "--signing-key",
                    base64.b64encode(priv_raw).decode("ascii"),
                ]
            )
            m_bytes = (dist / "BOOTFORGE_MANIFEST.json").read_bytes()
            sig_bytes = (dist / "BOOTFORGE_MANIFEST.json.sig").read_bytes()

            rel_json = json.dumps(
                {
                    "tag_name": "v1.2.0",
                    "html_url": "https://github.com/NiceMank/BootForge/releases/tag/v1.2.0",
                    "body": "v1.2.0",
                    "published_at": "2026-11-14T20:00:00Z",
                    "assets": [
                        {
                            "name": "BOOTFORGE_MANIFEST.json",
                            "browser_download_url": "https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BOOTFORGE_MANIFEST.json",
                        },
                        {
                            "name": "BOOTFORGE_MANIFEST.json.sig",
                            "browser_download_url": "https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BOOTFORGE_MANIFEST.json.sig",
                        },
                    ],
                }
            ).encode("utf-8")

            # 1. Invalid / corrupted signature rejected by check_for_update
            bad_sig = base64.b64encode(b"\xaa" * 64).decode("ascii").encode("ascii")
            with mock.patch(
                "engine.updater.fetch_https_bytes",
                side_effect=lambda url, **_kw: rel_json
                if url.endswith("/releases/latest")
                else (m_bytes if url.endswith("BOOTFORGE_MANIFEST.json") else bad_sig),
            ):
                res_badsig = updater.check_for_update(current_version="1.0.1", trusted_keys=keys)
            self.assertEqual(res_badsig.status, "invalid_signature")
            self.assertFalse(res_badsig.verified)

            # 2. Tampered manifest rejected by check_for_update
            m_dict = json.loads(m_bytes.decode("utf-8"))
            m_dict["assets"]["linux_deb"]["sha256"] = "0" * 64
            tampered_m = json.dumps(m_dict).encode("utf-8")
            with mock.patch(
                "engine.updater.fetch_https_bytes",
                side_effect=lambda url, **_kw: rel_json
                if url.endswith("/releases/latest")
                else (tampered_m if url.endswith("BOOTFORGE_MANIFEST.json") else sig_bytes),
            ):
                res_tampered = updater.check_for_update(current_version="1.0.1", trusted_keys=keys)
            self.assertEqual(res_tampered.status, "invalid_signature")
            self.assertFalse(res_tampered.verified)

            # 3. Signed downgrade / version mismatch in manifest blocked
            old_m = json.dumps(
                {**json.loads(m_bytes.decode("utf-8")), "version": "1.0.0", "tag": "v1.0.0"}
            ).encode("utf-8")
            old_sig = base64.b64encode(priv.sign(old_m)).decode("ascii").encode("ascii")
            with mock.patch(
                "engine.updater.fetch_https_bytes",
                side_effect=lambda url, **_kw: rel_json
                if url.endswith("/releases/latest")
                else (old_m if url.endswith("BOOTFORGE_MANIFEST.json") else old_sig),
            ):
                res_downgrade = updater.check_for_update(current_version="1.0.1", trusted_keys=keys)
            self.assertEqual(res_downgrade.status, "downgrade_blocked")
            self.assertFalse(res_downgrade.verified)

            # 4. Asset substituted / wrong SHA-256 over real download_https_atomic fails cleanly with no leftover file
            upd_deb = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.2.0",
                tag_name="v1.2.0",
                verified=True,
                asset_kind="linux_deb",
                asset_name="bootforge_1.2.0_all.deb",
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.2.0/bootforge_1.2.0_all.deb",
                asset_sha256=hashlib.sha256(b"EXPECTED-DEB-PAYLOAD").hexdigest(),
            )
            dl_dir = Path(tmp) / "dl"

            class _FakeResponse(io.BytesIO):
                def __init__(self, payload: bytes, url: str, fail_midstream: bool = False) -> None:
                    super().__init__(payload)
                    self._url = url
                    self._fail_midstream = fail_midstream
                    self.headers = {"Content-Length": str(len(payload))}
                    self._reads = 0

                def geturl(self) -> str:
                    return self._url

                def read(self, size: int = -1) -> bytes:
                    self._reads += 1
                    if self._fail_midstream and self._reads >= 2:
                        raise ConnectionResetError("Simulated interrupted network stream")
                    return super().read(size)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.close()
                    return False

            class _FakeOpener:
                def __init__(self, payload: bytes, fail_midstream: bool = False) -> None:
                    self.payload = payload
                    self.fail_midstream = fail_midstream

                def open(self, req, timeout: int = 45):
                    return _FakeResponse(self.payload, req.full_url, fail_midstream=self.fail_midstream)

            with mock.patch("engine.net_security.build_https_opener", return_value=_FakeOpener(b"EVIL-SUBSTITUTED-DEB")):
                with self.assertRaises(ValueError):
                    updater.download_verified_update(upd_deb, dest_dir=dl_dir)
            self.assertEqual(list(dl_dir.glob("*")), [])

            # 5. Interrupted download cleans up .part files without leaving partial asset
            with mock.patch(
                "engine.net_security.build_https_opener",
                return_value=_FakeOpener(b"PARTIAL-DATA", fail_midstream=True),
            ):
                with self.assertRaises(ConnectionResetError):
                    updater.download_verified_update(upd_deb, dest_dir=dl_dir)
            self.assertEqual(list(dl_dir.glob("*")), [])

            with mock.patch("engine.net_security.build_https_opener", return_value=_FakeOpener(b"EXPECTED-DEB-PAYLOAD")):
                with self.assertRaises(InterruptedError):
                    updater.download_verified_update(upd_deb, dest_dir=dl_dir, cancel_check=lambda: True)
            self.assertEqual(list(dl_dir.glob("*")), [])

            # 6. Windows NSIS setup installer invokes ShellExecuteW("runas", ..., "/S", ...) and handles UAC refusal
            exe_file = Path(tmp) / "BootForge-1.2.0-win64-setup.exe"
            exe_file.write_bytes(b"MZ-WIN-NSIS-INSTALLER")
            upd_exe = updater.UpdateCheckResult(
                status="available",
                current_version="1.0.1",
                latest_version="1.2.0",
                tag_name="v1.2.0",
                verified=True,
                asset_kind="windows_setup",
                asset_name="BootForge-1.2.0-win64-setup.exe",
                asset_url="https://github.com/NiceMank/BootForge/releases/download/v1.2.0/BootForge-1.2.0-win64-setup.exe",
                asset_sha256=hashlib.sha256(b"MZ-WIN-NSIS-INSTALLER").hexdigest(),
            )
            fake_shell32 = mock.MagicMock()
            fake_shell32.ShellExecuteW.return_value = 42
            fake_windll = mock.MagicMock(shell32=fake_shell32)
            with (
                mock.patch("engine.updater.sys.platform", "win32"),
                mock.patch.object(updater.ctypes, "windll", fake_windll, create=True),
            ):
                res_win_ok = updater.apply_downloaded_update(exe_file, upd_exe)
            self.assertEqual(res_win_ok["action"], "launched_win_setup")
            self.assertTrue(res_win_ok["should_quit"])
            fake_shell32.ShellExecuteW.assert_called_once_with(None, "runas", str(exe_file.resolve()), "/S", None, 1)

            fake_shell32.ShellExecuteW.return_value = 5
            with (
                mock.patch("engine.updater.sys.platform", "win32"),
                mock.patch.object(updater.ctypes, "windll", fake_windll, create=True),
            ):
                with self.assertRaises(RuntimeError):
                    updater.apply_downloaded_update(exe_file, upd_exe)
