#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$ROOT_DIR'); from bootforge_ui import __version__; print(__version__)")"
OUT_DIR="$ROOT_DIR/release"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

mkdir -p "$OUT_DIR"

PKG_NAME="bootforge"
DEB_ROOT="$STAGING/${PKG_NAME}_${VERSION}_all"

# Application payload -> /usr/share/bootforge
mkdir -p "$DEB_ROOT/usr/share/bootforge" \
         "$DEB_ROOT/usr/bin" \
         "$DEB_ROOT/usr/share/applications" \
         "$DEB_ROOT/usr/share/polkit-1/actions" \
         "$DEB_ROOT/usr/share/doc/$PKG_NAME" \
         "$DEB_ROOT/DEBIAN"

cp -a "$ROOT_DIR/bootforge" "$DEB_ROOT/usr/share/bootforge/bootforge"
cp -a "$ROOT_DIR/bootforge_ui" "$DEB_ROOT/usr/share/bootforge/"
cp -a "$ROOT_DIR/engine" "$DEB_ROOT/usr/share/bootforge/"
cp -a "$ROOT_DIR/assets" "$DEB_ROOT/usr/share/bootforge/"
find "$DEB_ROOT/usr/share/bootforge" -type d -name '__pycache__' -exec rm -rf {} +
find "$DEB_ROOT/usr/share/bootforge" -type d -exec chmod 0755 {} +
find "$DEB_ROOT/usr/share/bootforge" -type f -exec chmod 0644 {} +

chmod 0755 "$DEB_ROOT/usr/share/bootforge/bootforge"
chmod 0755 "$DEB_ROOT/usr/share/bootforge/engine/bootforge_engine.py"

# /usr/bin/bootforge symlink (resolves to /usr/share/bootforge/bootforge)
ln -s ../share/bootforge/bootforge "$DEB_ROOT/usr/bin/bootforge"

# Desktop entry & Polkit policy
install -m 0644 "$ROOT_DIR/data/io.github.nicemank.BootForge.desktop" \
  "$DEB_ROOT/usr/share/applications/io.github.nicemank.BootForge.desktop"
install -m 0644 "$ROOT_DIR/data/io.github.nicemank.bootforge.policy" \
  "$DEB_ROOT/usr/share/polkit-1/actions/io.github.nicemank.bootforge.policy"

# Hicolor icons
for sz in 16 32 48 64 128 256 512; do
  icon_src="$ROOT_DIR/build/icons/${sz}x${sz}.png"
  if [[ -f "$icon_src" ]]; then
    install -Dm 0644 "$icon_src" \
      "$DEB_ROOT/usr/share/icons/hicolor/${sz}x${sz}/apps/bootforge.png"
  fi
done
if [[ -f "$ROOT_DIR/assets/icon.svg" ]]; then
  install -Dm 0644 "$ROOT_DIR/assets/icon.svg" \
    "$DEB_ROOT/usr/share/icons/hicolor/scalable/apps/bootforge.svg"
fi

# Copyright
install -m 0644 "$ROOT_DIR/LICENSE" "$DEB_ROOT/usr/share/doc/$PKG_NAME/copyright"

find "$DEB_ROOT/usr" -type d -exec chmod 0755 {} +

INSTALLED_SIZE="$(du -sk "$DEB_ROOT/usr" | awk '{print $1}')"

cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: bootforge
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Maintainer: NiceMank <https://github.com/NiceMank/BootForge>
Homepage: https://github.com/NiceMank/BootForge
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, pkexec | policykit-1, 7zip | p7zip-full, parted, gdisk, dosfstools, ntfs-3g, exfatprogs, e2fsprogs, util-linux, udisks2
Recommends: wimtools, qemu-system-x86, ovmf, mtools, xorriso, smartmontools
Description: Création de clés USB démarrables Windows et Linux sous Linux
 Interface graphique native GTK4/Libadwaita permettant de créer des clés USB
 démarrables Windows 10/11 ou Linux (formatage GPT/MBR, UEFI:NTFS, découpe WIM,
 persistance ext4, options autounattend.xml) ainsi que des clés multi-systèmes
 Ventoy, avec un mode Simple et un mode Avancé.
EOF

cat > "$DEB_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
EOF
chmod 0755 "$DEB_ROOT/DEBIAN/postinst"

DEB_OUT="$OUT_DIR/${PKG_NAME}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DEB_OUT"

# Clean portable source tarball (ready to unpack & run ./bootforge)
TAR_STAGING="$STAGING/BootForge-${VERSION}-linux"
mkdir -p "$TAR_STAGING"
cp -a "$ROOT_DIR/bootforge" \
      "$ROOT_DIR/bootforge_ui" \
      "$ROOT_DIR/engine" \
      "$ROOT_DIR/assets" \
      "$ROOT_DIR/LICENSE" \
      "$ROOT_DIR/README.md" \
      "$TAR_STAGING/"
find "$TAR_STAGING" -type d -name '__pycache__' -exec rm -rf {} +
TAR_OUT="$OUT_DIR/BootForge-${VERSION}-linux.tar.gz"
tar -C "$STAGING" -czf "$TAR_OUT" "BootForge-${VERSION}-linux"

echo "Built packages:"
ls -lh "$DEB_OUT" "$TAR_OUT"
