#!/usr/bin/env bash
#
# Build AppImage pour EduSync AD (Linux)
# Usage : ./build/appimage/build-appimage.sh [version]
#
# Sortie : dist/EduSyncAD-linux.AppImage + dist/edusync-ad-linux-amd64.tar.gz

set -euo pipefail

VERSION="${1:-dev}"
APP="edusync-ad"
OUT="dist"
APPDIR="${OUT}/EduSyncAD.AppDir"

# Nettoyer
rm -rf "$APPDIR" "${OUT}/${APP}-linux-amd64.AppImage" "${OUT}/${APP}-linux-amd64.tar.gz"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# 1. Build du binaire
echo "→ Build binaire Go..."
CGO_ENABLED=1 go build -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o "${APPDIR}/usr/bin/${APP}" ./cmd/edusync-ad

# 2. Copier assets
echo "→ Copie assets..."
cp assets/icon.svg "${APPDIR}/usr/share/icons/hicolor/256x256/apps/edusync-ad.svg"

# 3. .desktop file
cat > "${APPDIR}/usr/share/applications/edusync-ad.desktop" <<EOF
[Desktop Entry]
Name=EduSync AD
Comment=Gestion du cycle de vie des comptes Active Directory
Exec=edusync-ad
Icon=edusync-ad
Terminal=false
Type=Application
Categories=System;Administration;
StartupNotify=true
EOF

# 4. AppRun (point d'entrée AppImage)
cat > "${APPDIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH:-}"
exec "${HERE}/usr/bin/edusync-ad" "$@"
EOF
chmod +x "${APPDIR}/AppRun"

# 5. Vérifier/Installer appimagetool
APPIMAGETOOL="/tmp/appimagetool-x86_64.AppImage"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "→ Téléchargement appimagetool..."
    curl -sSL -o "$APPIMAGETOOL" "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
fi

# 6. Générer l'AppImage
echo "→ Génération AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "${OUT}/EduSyncAD-linux.AppImage"

# 7. Créer aussi l'archive tar.gz (binaire + .desktop + icône)
echo "→ Création archive tar.gz..."
TAR_DIR="${OUT}/edusync-ad-linux-amd64"
mkdir -p "$TAR_DIR"
cp "${APPDIR}/usr/bin/${APP}" "${TAR_DIR}/"
cp "${APPDIR}/usr/share/applications/edusync-ad.desktop" "${TAR_DIR}/"
cp "${APPDIR}/usr/share/icons/hicolor/256x256/apps/edusync-ad.svg" "${TAR_DIR}/"
cat > "${TAR_DIR}/README.txt" <<EOF
EduSync AD ${VERSION} - Linux

Installation:
  1. Copier edusync-ad dans /usr/local/bin/ ou ~/bin/
  2. Copier edusync-ad.desktop dans ~/.local/share/applications/
  3. Copier edusync-ad.svg dans ~/.local/share/icons/hicolor/256x256/apps/
  4. Lancez depuis le menu applications ou en terminal: edusync-ad

Dépendances requises:
  - libwebkit2gtk-4.1-0
  - libgtk-4-1
  - libsoup-3.0-0
  - glib2

Ou utilisez l'AppImage (EduSyncAD-linux.AppImage) qui embarque tout.
EOF
tar -czf "${OUT}/edusync-ad-linux-amd64.tar.gz" -C "${OUT}" "edusync-ad-linux-amd64"

# 8. SHA256
echo "→ Calcul SHA256..."
sha256sum "${OUT}/EduSyncAD-linux.AppImage" > "${OUT}/EduSyncAD-linux.AppImage.sha256"
sha256sum "${OUT}/edusync-ad-linux-amd64.tar.gz" > "${OUT}/edusync-ad-linux-amd64.tar.gz.sha256"

echo
echo "✅ Artefacts Linux générés dans ${OUT}/ :"
ls -lh "${OUT}/EduSyncAD-linux.AppImage" "${OUT}/edusync-ad-linux-amd64.tar.gz" "${OUT}/"*.sha256 2>/dev/null