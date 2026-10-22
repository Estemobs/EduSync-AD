#!/usr/bin/env bash
#
# Matrice de cross-build d'EduSync-AD.
#
#   ./build/cross-build.sh            # toutes les cibles possibles
#   ./build/cross-build.sh cli        # uniquement la CLI
#   ./build/cross-build.sh desktop    # uniquement l'application desktop
#   VERSION=1.18.0 ./build/cross-build.sh
#
# Philosophie (ROADMAP_GO_MIGRATION.md §9) :
#   * la CLI est 100 % Go pur (CGO_ENABLED=0) → toutes les cibles se
#     compilent depuis n'importe quelle machine, sans outil système ;
#   * l'application desktop embarque WebView (cgo) → chaque cible exige sa
#     chaîne d'outillage. Le script détecte ce qui est disponible et
#     signale explicitement ce qu'il n'a PAS pu produire (jamais de
#     « succès » silencieux).
set -euo pipefail

VERSION="${VERSION:-$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)}"
VERSION="${VERSION:-dev}"
OUT="${OUT:-dist}"
GOFLAGS_BUILD="-s -w -X main.version=${VERSION}"

CLI="edusync-cli"
APP="edusync-ad"

# suffixe selon la cible (évite les collisions d'extension)
suffixe() {
  local goos="$1"
  case "$goos" in
    windows) echo ".exe" ;;
    *) echo "" ;;
  esac
}

# ─── CLI : compilation croisée pure ─────────────────────────────────────
build_cli() {
  local goos="$1" goarch="$2"
  local sortie="${OUT}/${CLI}-${goos}-${goarch}$(suffixe "$goos")"

  echo "→ CLI ${goos}/${goarch}"
  if GOOS="$goos" GOARCH="$goarch" CGO_ENABLED=0 \
      go build -trimpath -ldflags="${GOFLAGS_BUILD}" -o "$sortie" ./cmd/edusync-cli; then
    OK+=("$sortie")
  else
    ECHECS+=("CLI ${goos}/${goarch}")
  fi
}

# ─── Application desktop : cgo obligatoire ──────────────────────────────
build_desktop() {
  local goos="$1" goarch="$2"
  local sortie="${OUT}/${APP}-${goos}-${goarch}$(suffixe "$goos")"

  # Détection de la chaîne d'outillage requise.
  case "${goos}/${goarch}" in
    linux/amd64)
      if ! command -v pkg-config >/dev/null || ! pkg-config --exists gtk4 webkitgtk-6.0 libsoup-3.0; then
        echo "⏭  Desktop linux/amd64 ignoré : libgtk-4-dev / libwebkitgtk-6.0-dev absents"
        SAUTS+=("desktop linux/amd64 (gtk4/webkitgtk-6.0 manquant)")
        return
      fi
      cgo=1
      ;;
    windows/amd64)
      if ! command -v x86_64-w64-mingw32-gcc >/dev/null; then
        echo "⏭  Desktop windows/amd64 ignoré : mingw-w64 absent"
        SAUTS+=("desktop windows/amd64 (mingw-w64 manquant)")
        return
      fi
      cgo=1
      export CC=x86_64-w64-mingw32-gcc
      ;;
    darwin/*)
      echo "⏭  Desktop ${goos}/${goarch} ignoré : nécessite osxcross (build depuis macOS)"
      SAUTS+=("desktop ${goos}/${goarch} (osxcross manquant)")
      return
      ;;
    *)
      echo "⏭  Desktop ${goos}/${goarch} ignoré : cible non supportée"
      SAUTS+=("desktop ${goos}/${goarch} (non supporté)")
      return
      ;;
  esac

  echo "→ Desktop ${goos}/${goarch}"
  if GOOS="$goos" GOARCH="$goarch" CGO_ENABLED="$cgo" \
      go build -trimpath -ldflags="${GOFLAGS_BUILD}" -o "$sortie" ./cmd/edusync-ad; then
    OK+=("$sortie")
  else
    ECHECS+=("desktop ${goos}/${goarch}")
  fi
  unset CC
}

mkdir -p "$OUT"
OK=()
ECHECS=()
SAUTS=()

MODE="${1:-all}"

if [ "$MODE" = "all" ] || [ "$MODE" = "cli" ]; then
  build_cli linux amd64
  build_cli linux arm64
  build_cli windows amd64
  build_cli darwin amd64
  build_cli darwin arm64
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "desktop" ]; then
  build_desktop linux amd64
  build_desktop windows amd64
  build_desktop darwin arm64
fi

echo
echo "─── Récapitulatif (version ${VERSION}) ───────────────────────────"
for f in "${OK[@]:-}"; do
  [ -n "$f" ] && printf '  ✅ %10s  %s\n' "$(du -h "$f" | cut -f1)" "$f"
done
for f in "${SAUTS[@]:-}"; do
  [ -n "$f" ] && printf '  ⏭  ignoré : %s\n' "$f"
done
for f in "${ECHECS[@]:-}"; do
  [ -n "$f" ] && printf '  ❌ échec  : %s\n' "$f"
done

if [ "${#ECHECS[@]}" -gt 0 ]; then
  echo
  echo "Au moins une cible n'a pas pu être produite." >&2
  exit 1
fi
