#!/usr/bin/env bash
#
# Сборка дистрибутивов PenguinCraft в каталог dist/:
#
#   penguincraft_<версия>_all.deb              — системная установка (apt)
#   PenguinCraft-<версия>-installer.sh         — самораспаковывающийся установщик
#
# Использование:  ./packaging/build.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
BUILD="$ROOT/build"
PKG="$ROOT/packaging"

VERSION="$(python3 - "$ROOT/penguincraft/__init__.py" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
print(re.search(r'APP_VERSION = "([^"]+)"', text).group(1))
PY
)"
APP_ID="penguincraft"

if [ ! -f "$ROOT/penguincraft/ui/assets/$APP_ID.png" ]; then
    echo "Не найдена иконка penguincraft/ui/assets/$APP_ID.png" >&2
    exit 1
fi

echo "PenguinCraft $VERSION — сборка дистрибутивов"
rm -rf "$BUILD"
mkdir -p "$DIST" "$BUILD"

# ---------------------------------------------------------------------------
# .deb
# ---------------------------------------------------------------------------
build_deb() {
    local stage="$BUILD/deb"
    local pydir="$stage/usr/lib/python3/dist-packages"
    local bindir="$stage/usr/bin"
    local appsdir="$stage/usr/share/applications"
    local icondir="$stage/usr/share/icons/hicolor/256x256/apps"
    local docdir="$stage/usr/share/doc/$APP_ID"

    mkdir -p "$stage/DEBIAN" "$pydir" "$bindir" "$appsdir" "$icondir" "$docdir"

    # Код (без кешей байткода).
    tar -cf - -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' "$APP_ID" \
        | tar -xf - -C "$pydir"

    # Команда.
    cat > "$bindir/$APP_ID" <<'EOF'
#!/bin/sh
exec python3 -m penguincraft "$@"
EOF
    chmod 755 "$bindir/$APP_ID"

    # Пункт меню.
    cat > "$appsdir/$APP_ID.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=PenguinCraft
GenericName=Minecraft Launcher
Comment=Лёгкий лаунчер Minecraft для Linux и ChromeOS
Comment[en]=Lightweight Minecraft launcher for Linux and ChromeOS
Exec=penguincraft gui
TryExec=penguincraft
Icon=penguincraft
Terminal=false
Categories=Game;ActionGame;
Keywords=Minecraft;Game;Launcher;Mods;ChromeOS;
StartupNotify=true
StartupWMClass=Penguincraft
EOF
    chmod 644 "$appsdir/$APP_ID.desktop"

    cp -f "$ROOT/penguincraft/ui/assets/$APP_ID.png" "$icondir/$APP_ID.png"
    cp -f "$ROOT/README.md" "$docdir/README.md"
    cp -f "$PKG/deb/copyright" "$docdir/copyright"

    cp -f "$PKG/deb/postinst" "$stage/DEBIAN/postinst"
    cp -f "$PKG/deb/postrm" "$stage/DEBIAN/postrm"
    chmod 755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm"

    local size
    size="$(du -sk "$stage/usr" | cut -f1)"
    sed -e "s/@VERSION@/$VERSION/g" -e "s/@SIZE@/$size/g" \
        "$PKG/deb/control.in" > "$stage/DEBIAN/control"

    # Монтирование рабочего каталога может давать слишком «широкие» права —
    # dpkg-deb их не принимает, поэтому выставляем нормальные явно.
    find "$stage" -type d -exec chmod 755 {} +
    find "$stage" -type f -exec chmod 644 {} +
    chmod 755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm" "$bindir/$APP_ID"

    dpkg-deb --build --root-owner-group "$stage" "$DIST/${APP_ID}_${VERSION}_all.deb" >/dev/null
}

# ---------------------------------------------------------------------------
# Самораспаковывающийся .sh-установщик
# ---------------------------------------------------------------------------
build_installer() {
    local target="$DIST/PenguinCraft-$VERSION-installer.sh"

    tar -czf "$BUILD/payload.tar.gz" -C "$ROOT" \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
        "$APP_ID" run.sh README.md pyproject.toml "$APP_ID.desktop"

    sed -e "s/@VERSION@/$VERSION/g" -e "s/@APP_ID@/$APP_ID/g" \
        "$PKG/installer.sh.in" > "$target"
    base64 "$BUILD/payload.tar.gz" >> "$target"
    chmod 755 "$target"
}

build_deb
build_installer

echo
echo "Готово:"
ls -lh "$DIST" | tail -n +2 | awk '{ printf "  %-44s %s\n", $9, $5 }'
echo
echo "SHA256:"
( cd "$DIST" && sha256sum ./* | sed 's/  /  /' | awk '{ printf "  %s  %s\n", $1, $2 }' )
