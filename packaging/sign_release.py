#!/usr/bin/env python3
"""Release manifest generator and Ed25519 signer for BootForge CI/CD."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _load_ed25519_private(raw_key: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    clean = raw_key.strip()
    if len(clean) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean):
        seed = bytes.fromhex(clean)
    else:
        seed = base64.b64decode(clean, validate=True)
    if len(seed) != 32:
        raise ValueError("La clé privée Ed25519 doit faire exactement 32 octets (seed).")
    return Ed25519PrivateKey.from_private_bytes(seed)


def _is_inside_repo(target: Path, repo_root: Path) -> bool:
    """Return True if target path resolves inside the repository root."""
    try:
        return target.resolve().is_relative_to(repo_root.resolve())
    except OSError:
        return True


def cmd_keygen(args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    repo_root = Path(__file__).resolve().parent.parent
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
    priv_b64 = base64.b64encode(priv_raw).decode("ascii")

    if args.private_out:
        out_path = Path(args.private_out).expanduser()
        if _is_inside_repo(out_path, repo_root):
            print(
                f"ERREUR SÉCURITÉ : refus d'écrire la clé privée dans le dépôt ({out_path}).",
                file=sys.stderr,
            )
            return 2
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (priv_b64 + "\n").encode("ascii"))
        finally:
            os.close(fd)
        os.chmod(out_path, 0o600)
        print(f"Private key saved safely (mode 0600) to: {out_path.resolve()}")
        print("# Import its content into GitHub Actions secret BOOTFORGE_SIGNING_KEY")
    elif args.unsafe_stdout:
        print("# KEEP PRIVATE KEY SECRET — store in GitHub Actions secret BOOTFORGE_SIGNING_KEY")
        print(f"PRIVATE_KEY_B64={priv_b64}")
    else:
        print(
            "ERREUR : spécifiez --private-out <fichier_hors_depot> pour enregistrer la clé privée en mode 0600 "
            "(ou --unsafe-stdout explicitement).",
            file=sys.stderr,
        )
        return 2

    print("# SAFE TO COMMIT — paste into TRUSTED_RELEASE_PUBLIC_KEYS['bootforge-release-2026'] in engine/updater.py")
    print(f"PUBLIC_KEY_HEX={pub_raw.hex()}")
    return 0


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fin:
        while True:
            buf = fin.read(256 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest().lower()


def _classify_asset(filename: str) -> str | None:
    low = filename.lower()
    if low.endswith(".deb"):
        return "linux_deb"
    if low.endswith("-linux.tar.gz"):
        return "linux_tar"
    if low.endswith("-win64-setup.exe"):
        return "windows_setup"
    if low.endswith("-win64-portable.zip"):
        return "windows_portable"
    return None


def cmd_build_manifest(args: argparse.Namespace) -> int:
    tag = args.tag.strip()
    version = tag.lstrip("v")
    repo = args.repo.strip()
    dist_dir = Path(args.dist_dir).resolve()
    assets: Dict[str, Any] = {}

    for item in sorted(dist_dir.iterdir()):
        if not item.is_file():
            continue
        kind = _classify_asset(item.name)
        if not kind:
            continue
        assets[kind] = {
            "name": item.name,
            "url": f"https://github.com/{repo}/releases/download/{tag}/{item.name}",
            "sha256": _sha256_file(item),
            "size": item.stat().st_size,
        }

    manifest = {
        "schema_version": 1,
        "product": "BootForge",
        "repo": repo,
        "key_id": args.key_id,
        "version": version,
        "tag": tag,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assets": assets,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    out_manifest = dist_dir / "BOOTFORGE_MANIFEST.json"
    out_manifest.write_bytes(manifest_bytes)
    print(f"Wrote {out_manifest} ({len(assets)} assets)")

    raw_priv = args.signing_key or os.environ.get("BOOTFORGE_SIGNING_KEY", "")
    if raw_priv.strip():
        priv = _load_ed25519_private(raw_priv)
        sig = priv.sign(manifest_bytes)
        out_sig = dist_dir / "BOOTFORGE_MANIFEST.json.sig"
        out_sig.write_text(f"# key_id: {args.key_id}\n{base64.b64encode(sig).decode('ascii')}\n", encoding="utf-8")
        print(f"Wrote detached Ed25519 signature {out_sig}")
    elif args.require_signature:
        print("ERREUR : BOOTFORGE_SIGNING_KEY manquant avec --require-signature.", file=sys.stderr)
        return 3
    else:
        print("BOOTFORGE_SIGNING_KEY not provided; skipped .sig generation.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BootForge release manifest generator & Ed25519 signer")
    parser.add_argument("--keygen", action="store_true", help="Generate a new Ed25519 keypair")
    parser.add_argument(
        "--private-out",
        default="",
        help="File path outside repo to save base64 private key with 0600 permissions",
    )
    parser.add_argument(
        "--unsafe-stdout",
        action="store_true",
        help="Explicitly allow printing private key to stdout (unsafe for logs)",
    )
    parser.add_argument("--dist-dir", help="Directory containing built release artifacts")
    parser.add_argument("--tag", help="Release git tag (e.g. v1.0.2)")
    parser.add_argument("--repo", default="NiceMank/BootForge", help="GitHub owner/repo")
    parser.add_argument("--key-id", default="bootforge-release-2026", help="Signing key identifier")
    parser.add_argument("--signing-key", default="", help="Ed25519 private seed (hex or base64)")
    parser.add_argument(
        "--require-signature",
        action="store_true",
        help="Fail with non-zero exit code if signing key is missing",
    )
    args = parser.parse_args(argv)

    if args.keygen:
        return cmd_keygen(args)
    if not args.dist_dir or not args.tag:
        parser.error("--dist-dir and --tag are required unless --keygen is passed")
    return cmd_build_manifest(args)


if __name__ == "__main__":
    raise SystemExit(main())
