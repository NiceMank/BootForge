#!/usr/bin/env python3
"""Shared network transport security, HTTPS redirect guard, and atomic verified downloads."""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Optional

GITHUB_ALLOWED_HOSTS = (
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)

CATALOG_INITIAL_HOSTS = (
    "releases.ubuntu.com",
    "mirrors.kernel.org",
    "cdimage.debian.org",
    "fastly-cdn.system-rescue.org",
    "downloads.sourceforge.net",
    "cdimage.kali.org",
    "www.system-rescue.org",
    "gparted.org",
)


class _StrictHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject any non-HTTPS redirect and enforce optional host allowlist on every hop."""

    def __init__(self, allowed_hosts: Optional[Iterable[str]] = None) -> None:
        super().__init__()
        self._allowed_hosts = tuple(h.lower() for h in allowed_hosts) if allowed_hosts else None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        abs_url = urllib.parse.urljoin(req.full_url, newurl)
        validate_https_url(abs_url, allowed_hosts=self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, abs_url)


def host_matches_allowlist(host: str, allowed_hosts: Iterable[str]) -> bool:
    clean = (host or "").strip().lower().rstrip(".")
    if not clean:
        return False
    for pattern in allowed_hosts:
        p = pattern.strip().lower().rstrip(".")
        if p.startswith("*."):
            suffix = p[1:]
            if clean.endswith(suffix) and len(clean) > len(suffix):
                return True
        elif clean == p:
            return True
    return False


def validate_https_url(url: str, allowed_hosts: Optional[Iterable[str]] = None) -> urllib.parse.ParseResult:
    """Validate URL uses strict HTTPS (no credentials in URL) and optional domain allowlist."""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https":
        raise ValueError(f"Protocole HTTPS obligatoire : {url!r}")
    if not parsed.hostname:
        raise ValueError(f"Hôte manquant dans l'URL : {url!r}")
    if parsed.username or parsed.password:
        raise ValueError("Identifiants intégrés dans l'URL interdits.")
    if allowed_hosts is not None and not host_matches_allowlist(parsed.hostname, allowed_hosts):
        raise ValueError(f"Domaine non autorisé pour le téléchargement sécurisé : {parsed.hostname}")
    return parsed


def build_https_opener(allowed_hosts: Optional[Iterable[str]] = None) -> urllib.request.OpenerDirector:
    """Create urllib opener verifying TLS certificates and forbidding HTTPS->HTTP downgrades."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        _StrictHttpsRedirectHandler(allowed_hosts=allowed_hosts),
    )


def fetch_https_bytes(
    url: str,
    *,
    allowed_hosts: Optional[Iterable[str]] = None,
    timeout: int = 20,
    max_bytes: int = 8 * 1024 * 1024,
    headers: Optional[dict[str, str]] = None,
) -> bytes:
    """Download bounded bytes over verified HTTPS."""
    validate_https_url(url, allowed_hosts=allowed_hosts)
    req_headers = {"User-Agent": "BootForge/1.1"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    opener = build_https_opener(allowed_hosts=allowed_hosts)
    with opener.open(req, timeout=timeout) as resp:
        validate_https_url(resp.geturl(), allowed_hosts=allowed_hosts)
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Réponse distante trop volumineuse (> {max_bytes} octets).")
    return data


def download_https_atomic(
    url: str,
    dest_path: Path,
    *,
    allowed_hosts: Optional[Iterable[str]] = None,
    redirect_allowed_hosts: Optional[Iterable[str]] = None,
    allow_any_https_redirect: bool = False,
    expected_sha256: Optional[str] = None,
    timeout: int = 45,
    chunk_size: int = 256 * 1024,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
) -> Path:
    """Stream download over strict HTTPS into a temp file, verify SHA-256, then atomically replace dest_path."""
    validate_https_url(url, allowed_hosts=allowed_hosts)
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if expected_sha256 is not None:
        norm_expected = expected_sha256.strip().lower()
        if not re.match(r"^[0-9a-f]{64}$", norm_expected):
            raise ValueError(f"Empreinte SHA-256 attendue invalide : {expected_sha256!r}")
    else:
        norm_expected = None

    if allow_any_https_redirect:
        redir_hosts = None
    else:
        redir_hosts = redirect_allowed_hosts if redirect_allowed_hosts is not None else allowed_hosts
    opener = build_https_opener(allowed_hosts=redir_hosts)
    req = urllib.request.Request(url, headers={"User-Agent": "BootForge/1.1"})

    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest_path.name}.", suffix=".part", dir=str(dest_path.parent))
    tmp_path = Path(tmp_name)
    hasher = hashlib.sha256()
    received = 0

    try:
        with os.fdopen(fd, "wb") as fout:
            with opener.open(req, timeout=timeout) as resp:
                validate_https_url(resp.geturl(), allowed_hosts=redir_hosts)
                total = int(resp.headers.get("Content-Length") or 0)
                t0 = time.monotonic()
                last_emit = t0
                while True:
                    if cancel_check and cancel_check():
                        raise InterruptedError("Téléchargement annulé.")
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    fout.write(chunk)
                    hasher.update(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if progress_cb and now - last_emit >= 0.2:
                        elapsed = max(now - t0, 0.01)
                        progress_cb(received, total, received / elapsed)
                        last_emit = now
            fout.flush()
            os.fsync(fout.fileno())

        if progress_cb:
            progress_cb(received, received, 0.0)

        if norm_expected and hasher.hexdigest().lower() != norm_expected:
            raise ValueError(
                f"Échec de vérification SHA-256 pour {dest_path.name} "
                f"(obtenu={hasher.hexdigest().lower()}, attendu={norm_expected})."
            )

        os.replace(tmp_path, dest_path)
        return dest_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
