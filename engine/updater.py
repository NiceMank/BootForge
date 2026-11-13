#!/usr/bin/env python3
"""Cryptographically verified GitHub Releases auto-updater for BootForge."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from engine.net_security import (
    GITHUB_ALLOWED_HOSTS,
    download_https_atomic,
    fetch_https_bytes,
    validate_https_url,
)
from engine.ventoy_manager import safe_extract_tar, safe_extract_zip

GITHUB_REPO = "NiceMank/BootForge"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
MANIFEST_FILENAME = "BOOTFORGE_MANIFEST.json"
MANIFEST_SIG_FILENAME = "BOOTFORGE_MANIFEST.json.sig"
PRODUCTION_KEY_ID = "bootforge-release-2026"

# Pinned Ed25519 public keys (64-hex chars = 32 raw bytes).
# Provision safely (never store private key in repo):
#   python3 packaging/sign_release.py --keygen --private-out ~/.config/bootforge-secrets/release.key
TRUSTED_RELEASE_PUBLIC_KEYS: Dict[str, str] = {
    PRODUCTION_KEY_ID: "3f452d9cc024643b69eee6d72ddda3ec9d62c6ea9148a9f8564bb398e79c7469",
}


def is_release_key_provisioned(
    key_id: str = PRODUCTION_KEY_ID,
    trusted_keys: Optional[Dict[str, str]] = None,
) -> bool:
    """Return True if a syntactically valid 32-byte Ed25519 hex public key is provisioned."""
    key_map = trusted_keys if trusted_keys is not None else TRUSTED_RELEASE_PUBLIC_KEYS
    hex_pub = str(key_map.get(key_id) or "").strip()
    return bool(re.match(r"^[0-9a-fA-F]{64}$", hex_pub))

# ---------------------------------------------------------------------------
# Pure-Python RFC 8032 Ed25519 Signature Verifier (zero runtime dependencies)
# ---------------------------------------------------------------------------
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)
_B_Y = (4 * pow(5, _P - 2, _P)) % _P


def _ed_xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_B_X = _ed_xrecover(_B_Y)
_B_EXT = (_B_X, _B_Y, 1, (_B_X * _B_Y) % _P)


def _ed_add(p: Tuple[int, int, int, int], q: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (2 * _D * t1 * t2) % _P
    d = (2 * z1 * z2) % _P
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _ed_scalarmult(pt: Tuple[int, int, int, int], scalar: int) -> Tuple[int, int, int, int]:
    res = (0, 1, 1, 0)
    cur = pt
    k = scalar
    while k > 0:
        if k & 1:
            res = _ed_add(res, cur)
        cur = _ed_add(cur, cur)
        k >>= 1
    return res


def _ed_point_equal(p: Tuple[int, int, int, int], q: Tuple[int, int, int, int]) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return ((x1 * z2 - x2 * z1) % _P == 0) and ((y1 * z2 - y2 * z1) % _P == 0)


def _ed_decode_point(enc: bytes) -> Optional[Tuple[int, int, int, int]]:
    if len(enc) != 32:
        return None
    y_int = int.from_bytes(enc, "little")
    sign = (y_int >> 255) & 1
    y = y_int & ((1 << 255) - 1)
    if y >= _P:
        return None
    x_sq = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x_sq, (_P + 3) // 8, _P)
    if (x * x - x_sq) % _P != 0:
        x = (x * _I) % _P
    if (x * x - x_sq) % _P != 0:
        return None
    if x == 0 and sign == 1:
        return None
    if (x & 1) != sign:
        x = _P - x
    return (x, y, 1, (x * y) % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify an Ed25519 detached signature according to RFC 8032."""
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
        return False
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    if not isinstance(message, (bytes, bytearray)):
        return False

    a_pt = _ed_decode_point(bytes(public_key))
    r_pt = _ed_decode_point(bytes(signature[:32]))
    if a_pt is None or r_pt is None:
        return False

    # Reject identity and all 8-torsion / small-order Ed25519 public keys ([8]A == O)
    a8 = _ed_add(a_pt, a_pt)
    a8 = _ed_add(a8, a8)
    a8 = _ed_add(a8, a8)
    if _ed_point_equal(a8, (0, 1, 1, 0)):
        return False

    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False

    h_digest = hashlib.sha512(bytes(signature[:32]) + bytes(public_key) + bytes(message)).digest()
    k = int.from_bytes(h_digest, "little") % _L

    sb_pt = _ed_scalarmult(_B_EXT, s)
    ka_pt = _ed_scalarmult(a_pt, k)
    rhs = _ed_add(r_pt, ka_pt)
    return _ed_point_equal(sb_pt, rhs)


# ---------------------------------------------------------------------------
# SemVer parsing & comparison
# ---------------------------------------------------------------------------
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: Tuple[Tuple[int, Any], ...] = ()

    @classmethod
    def parse(cls, raw: str) -> "SemVer":
        m = _SEMVER_RE.match(str(raw or "").strip())
        if not m:
            raise ValueError(f"Version SemVer invalide : {raw!r}")
        pre_raw = m.group(4)
        pre_parts: List[Tuple[int, Any]] = []
        if pre_raw:
            for tok in pre_raw.split("."):
                if tok.isdigit():
                    pre_parts.append((0, int(tok)))
                else:
                    pre_parts.append((1, tok))
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), tuple(pre_parts))

    def _cmp_key(self) -> Tuple[int, int, int, int, Tuple[Tuple[int, Any], ...]]:
        # Stable release (no prerelease) > prerelease for same major.minor.patch
        is_stable = 1 if not self.prerelease else 0
        return (self.major, self.minor, self.patch, is_stable, self.prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._cmp_key() < other._cmp_key()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._cmp_key() <= other._cmp_key()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._cmp_key() > other._cmp_key()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            suffix = ".".join(str(v) for _, v in self.prerelease)
            return f"{base}-{suffix}"
        return base


class UpdateVerificationError(RuntimeError):
    """Raised when update manifest signature, checksum, or version validation fails."""


@dataclass
class UpdateCheckResult:
    status: str  # up_to_date | available | invalid_signature | unprovisioned_key | missing_manifest | downgrade_blocked | error
    current_version: str
    latest_version: str = ""
    tag_name: str = ""
    release_url: str = ""
    changelog: str = ""
    published_at: str = ""
    verified: bool = False
    error_message: str = ""
    asset_kind: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_sha256: str = ""
    asset_size: int = 0
    manifest: Dict[str, Any] = field(default_factory=dict)


def get_current_version() -> str:
    try:
        from bootforge_ui import __version__ as ver

        return ver
    except Exception:
        try:
            from bootforge_win import __version__ as ver

            return ver
        except Exception:
            return "1.0.1"


def get_updates_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    d = base / "bootforge" / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def detect_install_channel(platform_name: Optional[str] = None) -> str:
    """Detect optimal update asset kind for current runtime platform."""
    plat = platform_name or sys.platform
    if plat == "win32":
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "uninstall.exe").is_file() or "Program Files" in str(exe_dir):
            return "windows_setup"
        return "windows_portable" if getattr(sys, "frozen", False) else "windows_setup"

    if Path("/usr/share/bootforge/engine/bootforge_engine.py").is_file():
        return "linux_deb"
    return "linux_tar"


def decode_signature_bytes(sig_raw: bytes) -> bytes:
    """Decode base64-encoded (or raw 64-byte) detached Ed25519 signature."""
    # Check raw 64-byte binary signatures BEFORE stripping whitespace (0x09/0x0a/0x0d/0x20 may be valid bytes)
    if len(sig_raw) == 64 and not re.match(rb"^[A-Za-z0-9+/=_-]+$", sig_raw):
        return bytes(sig_raw)
    stripped = sig_raw.strip()
    if len(stripped) == 64 and not re.match(rb"^[A-Za-z0-9+/=_-]+$", stripped):
        return stripped
    text = stripped.decode("utf-8", errors="ignore").strip()
    # Ignore optional comment lines (e.g. key_id: ...)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise UpdateVerificationError("Fichier signature .sig vide.")
    candidate = lines[-1]
    try:
        decoded = base64.b64decode(candidate, validate=True)
    except Exception as exc:
        raise UpdateVerificationError(f"Signature Base64 malformée : {exc}") from exc
    if len(decoded) != 64:
        raise UpdateVerificationError(f"Longueur de signature Ed25519 invalide ({len(decoded)} octets).")
    return decoded


def verify_manifest_bytes(
    manifest_bytes: bytes,
    sig_bytes: bytes,
    *,
    trusted_keys: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Verify Ed25519 signature and schema integrity of BOOTFORGE_MANIFEST.json."""
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise UpdateVerificationError(f"Manifeste JSON invalide : {exc}") from exc

    if not isinstance(manifest, dict):
        raise UpdateVerificationError("Structure du manifeste invalide.")
    if manifest.get("schema_version") != 1 or manifest.get("product") != "BootForge":
        raise UpdateVerificationError("Métadonnées produit/schéma incompatibles dans le manifeste.")
    if str(manifest.get("repo") or "").lower() != GITHUB_REPO.lower():
        raise UpdateVerificationError("Le dépôt déclaré dans le manifeste ne correspond pas à NiceMank/BootForge.")

    key_map = trusted_keys if trusted_keys is not None else TRUSTED_RELEASE_PUBLIC_KEYS
    key_id = str(manifest.get("key_id") or PRODUCTION_KEY_ID)
    hex_pub = str(key_map.get(key_id) or "").strip()
    if not hex_pub:
        raise UpdateVerificationError(f"UNPROVISIONED_KEY:{key_id}")
    if not re.match(r"^[0-9a-fA-F]{64}$", hex_pub):
        raise UpdateVerificationError(f"Clé publique Ed25519 configurée invalide pour {key_id}.")

    sig = decode_signature_bytes(sig_bytes)
    pub_bytes = bytes.fromhex(hex_pub)
    if not ed25519_verify(pub_bytes, manifest_bytes, sig):
        raise UpdateVerificationError("INVALID_SIGNATURE: Échec de vérification cryptographique Ed25519.")
    return manifest


def check_for_update(
    *,
    current_version: Optional[str] = None,
    trusted_keys: Optional[Dict[str, str]] = None,
    platform_name: Optional[str] = None,
    install_channel: Optional[str] = None,
) -> UpdateCheckResult:
    """Query GitHub latest release, verify signed manifest & SemVer anti-downgrade."""
    cur_ver_str = current_version or get_current_version()
    cur_ver = SemVer.parse(cur_ver_str)

    try:
        rel_raw = fetch_https_bytes(
            GITHUB_LATEST_RELEASE_API,
            allowed_hosts=GITHUB_ALLOWED_HOSTS,
            timeout=15,
            max_bytes=2 * 1024 * 1024,
            headers={"Accept": "application/vnd.github+json"},
        )
        rel = json.loads(rel_raw.decode("utf-8"))
    except Exception as exc:
        return UpdateCheckResult(
            status="error",
            current_version=cur_ver_str,
            error_message=str(exc),
        )

    tag_name = str(rel.get("tag_name") or "").strip()
    release_url = str(rel.get("html_url") or "")
    changelog = str(rel.get("body") or "")
    published_at = str(rel.get("published_at") or "")
    if not tag_name:
        return UpdateCheckResult(status="error", current_version=cur_ver_str, error_message="Tag GitHub manquant.")

    try:
        remote_ver = SemVer.parse(tag_name)
    except ValueError as exc:
        return UpdateCheckResult(status="error", current_version=cur_ver_str, tag_name=tag_name, error_message=str(exc))

    if remote_ver <= cur_ver:
        return UpdateCheckResult(
            status="up_to_date",
            current_version=cur_ver_str,
            latest_version=str(remote_ver),
            tag_name=tag_name,
            release_url=release_url,
            changelog=changelog,
            published_at=published_at,
            verified=True,
        )

    assets = rel.get("assets") or []
    manifest_asset = next((a for a in assets if a.get("name") == MANIFEST_FILENAME), None)
    sig_asset = next((a for a in assets if a.get("name") == MANIFEST_SIG_FILENAME), None)
    if not manifest_asset or not sig_asset:
        return UpdateCheckResult(
            status="missing_manifest",
            current_version=cur_ver_str,
            latest_version=str(remote_ver),
            tag_name=tag_name,
            release_url=release_url,
            changelog=changelog,
            published_at=published_at,
            verified=False,
            error_message="Manifeste signé Ed25519 absent de cette release GitHub : installation automatique bloquée.",
        )

    try:
        manifest_bytes = fetch_https_bytes(
            str(manifest_asset["browser_download_url"]),
            allowed_hosts=GITHUB_ALLOWED_HOSTS,
            timeout=15,
            max_bytes=1024 * 1024,
        )
        sig_bytes = fetch_https_bytes(
            str(sig_asset["browser_download_url"]),
            allowed_hosts=GITHUB_ALLOWED_HOSTS,
            timeout=15,
            max_bytes=64 * 1024,
        )
        manifest = verify_manifest_bytes(manifest_bytes, sig_bytes, trusted_keys=trusted_keys)
    except UpdateVerificationError as exc:
        msg = str(exc)
        if msg.startswith("UNPROVISIONED_KEY:"):
            status = "unprovisioned_key"
            msg = "Clé publique Ed25519 de release non provisionnée : installation automatique refusée par sécurité."
        elif msg.startswith("INVALID_SIGNATURE:"):
            status = "invalid_signature"
        else:
            status = "invalid_signature"
        return UpdateCheckResult(
            status=status,
            current_version=cur_ver_str,
            latest_version=str(remote_ver),
            tag_name=tag_name,
            release_url=release_url,
            changelog=changelog,
            published_at=published_at,
            verified=False,
            error_message=msg,
        )

    # Anti-downgrade & tag/version binding check
    try:
        manifest_ver = SemVer.parse(str(manifest.get("version") or ""))
    except ValueError as exc:
        return UpdateCheckResult(
            status="invalid_signature",
            current_version=cur_ver_str,
            tag_name=tag_name,
            error_message=str(exc),
        )

    if str(manifest_ver) != str(remote_ver) or str(manifest.get("tag") or "") != tag_name or manifest_ver <= cur_ver:
        return UpdateCheckResult(
            status="downgrade_blocked",
            current_version=cur_ver_str,
            latest_version=str(manifest_ver),
            tag_name=tag_name,
            release_url=release_url,
            verified=False,
            error_message="Incohérence de version ou tentative de downgrade détectée dans le manifeste.",
        )

    channel = install_channel or detect_install_channel(platform_name=platform_name)
    m_assets = manifest.get("assets") or {}
    chosen = m_assets.get(channel)
    if not chosen and channel == "linux_deb":
        chosen = m_assets.get("linux_tar")
        channel = "linux_tar"
    elif not chosen and channel == "windows_setup":
        chosen = m_assets.get("windows_portable")
        channel = "windows_portable"

    if not isinstance(chosen, dict) or not chosen.get("url") or not chosen.get("sha256"):
        return UpdateCheckResult(
            status="error",
            current_version=cur_ver_str,
            latest_version=str(manifest_ver),
            tag_name=tag_name,
            release_url=release_url,
            error_message=f"Aucun paquet compatible ({channel}) dans le manifeste signé.",
        )

    asset_name = os.path.basename(str(chosen.get("name") or "")).strip()
    if not asset_name or "/" in asset_name or "\\" in asset_name:
        return UpdateCheckResult(
            status="invalid_signature",
            current_version=cur_ver_str,
            error_message="Nom de fichier asset invalide dans le manifeste.",
        )

    asset_url = str(chosen["url"]).strip()
    expected_prefix = f"https://github.com/{GITHUB_REPO}/releases/download/{tag_name}/"
    try:
        validate_https_url(asset_url, allowed_hosts=GITHUB_ALLOWED_HOSTS)
    except ValueError as exc:
        return UpdateCheckResult(
            status="invalid_signature",
            current_version=cur_ver_str,
            error_message=f"URL d'asset non autorisée : {exc}",
        )
    if not asset_url.startswith(expected_prefix):
        return UpdateCheckResult(
            status="invalid_signature",
            current_version=cur_ver_str,
            error_message="URL d'asset hors du dépôt/tag officiel BootForge.",
        )

    return UpdateCheckResult(
        status="available",
        current_version=cur_ver_str,
        latest_version=str(manifest_ver),
        tag_name=tag_name,
        release_url=release_url,
        changelog=changelog,
        published_at=published_at,
        verified=True,
        asset_kind=channel,
        asset_name=asset_name,
        asset_url=asset_url,
        asset_sha256=str(chosen["sha256"]).lower(),
        asset_size=int(chosen.get("size") or 0),
        manifest=manifest,
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fin:
        while True:
            buf = fin.read(256 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest().lower()


def download_verified_update(
    update: UpdateCheckResult,
    *,
    dest_dir: Optional[Path] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
    is_busy_fn: Optional[Callable[[], bool]] = None,
) -> Path:
    """Download an Ed25519-verified release asset atomically with strict SHA-256 verification."""
    if is_busy_fn and is_busy_fn():
        raise RuntimeError("Mise à jour différée : une opération USB est actuellement en cours.")
    if update.status != "available" or not update.verified:
        raise UpdateVerificationError("Cette mise à jour n'est pas cryptographiquement vérifiée.")
    if not update.asset_url or not re.match(r"^[0-9a-f]{64}$", update.asset_sha256):
        raise UpdateVerificationError("URL ou SHA-256 manquant pour l'asset de mise à jour.")

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", update.asset_name).lstrip(".")
    if not safe_name:
        raise ValueError("Nom de fichier de mise à jour invalide.")

    target_dir = (dest_dir or get_updates_cache_dir()).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_path = (target_dir / safe_name).resolve()
    if not dest_path.is_relative_to(target_dir):
        raise ValueError("Chemin cible hors du répertoire de cache.")

    return download_https_atomic(
        update.asset_url,
        dest_path,
        allowed_hosts=GITHUB_ALLOWED_HOSTS,
        expected_sha256=update.asset_sha256,
        timeout=60,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )


def apply_downloaded_update(
    asset_path: Path,
    update: UpdateCheckResult,
    *,
    is_busy_fn: Optional[Callable[[], bool]] = None,
    runner: Optional[Callable[[List[str]], Any]] = None,
) -> Dict[str, Any]:
    """Re-verify SHA-256 against signed manifest (anti-TOCTOU) and apply OS-specific update."""
    if is_busy_fn and is_busy_fn():
        raise RuntimeError("Mise à jour différée : une opération USB est actuellement en cours.")
    if not update.verified or update.status != "available":
        raise UpdateVerificationError("Refus d'installer une mise à jour non vérifiée.")

    resolved = Path(asset_path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Paquet de mise à jour introuvable : {resolved}")

    actual_sha = _file_sha256(resolved)
    if actual_sha != update.asset_sha256.lower():
        raise UpdateVerificationError(
            f"Échec de revérification SHA-256 avant installation ({actual_sha} != {update.asset_sha256})."
        )

    kind = update.asset_kind
    if kind == "linux_deb":
        if shutil.which("apt-get"):
            # Force root sandbox so apt-get can read .deb staged in user's ~/.cache/bootforge/updates (0700)
            cmd = ["pkexec", "apt-get", "-o", "APT::Sandbox::User=root", "install", "-y", str(resolved)]
        else:
            cmd = ["pkexec", "dpkg", "-i", str(resolved)]
        if runner:
            runner(cmd)
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"pkexec code {proc.returncode}")
        return {"action": "installed_deb", "should_restart": True, "path": str(resolved)}

    if kind == "linux_tar":
        stage_dir = (resolved.parent / f"extracted-{update.latest_version}").resolve()
        if not stage_dir.is_relative_to(resolved.parent):
            raise ValueError("Dossier d'extraction non sûr.")
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_tar(resolved, stage_dir)
        return {"action": "extracted_portable", "should_restart": False, "path": str(stage_dir)}

    if kind == "windows_setup":
        # BootForge installer uses NSIS (win_installer.nsi), whose silent flag is uppercase /S
        if runner:
            runner([str(resolved), "/S"])
        elif sys.platform == "win32":
            rc = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                str(resolved),
                "/S",
                None,
                1,
            )
            if int(rc) <= 32:
                raise RuntimeError(f"Élévation UAC refusée ou échouée (code {rc}).")
        return {"action": "launched_win_setup", "should_quit": True, "path": str(resolved)}

    if kind == "windows_portable":
        stage_dir = (resolved.parent / f"extracted-{update.latest_version}").resolve()
        if not stage_dir.is_relative_to(resolved.parent):
            raise ValueError("Dossier d'extraction non sûr.")
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(resolved, stage_dir)
        return {"action": "extracted_portable", "should_quit": False, "path": str(stage_dir)}

    raise ValueError(f"Type d'asset de mise à jour non géré : {kind}")
