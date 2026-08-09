#!/usr/bin/env bash
# Install TrueRedact for the current user: the `trueredact` command plus a
# desktop entry, so the drag-and-drop window can be opened without a terminal.
# Nothing is installed system-wide and nothing needs root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$HOME/.local/share/trueredact"
BINDIR="$HOME/.local/bin"
BIN="$BINDIR/trueredact"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP="$APPS/trueredact.desktop"
ICON="$ICONS/trueredact.svg"

uninstall() {
  rm -f "$DESKTOP" "$ICON"
  if command -v pipx >/dev/null && pipx list 2>/dev/null | grep -q trueredact; then
    pipx uninstall trueredact >/dev/null
  fi
  rm -rf "$PREFIX"
  [ -L "$BIN" ] && rm -f "$BIN"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
  echo "Removed the command, the private environment, and the desktop entry."
  exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

echo "Installing TrueRedact for $USER"

if command -v pipx >/dev/null; then
  pipx install --force "$REPO" >/dev/null
  TARGET="$HOME/.local/bin/trueredact"
else
  # Debian and Ubuntu mark the system Python externally managed (PEP 668), so a
  # user-site install is refused. A dedicated environment sidesteps that without
  # touching the system Python, and needs no extra tooling.
  echo "  pipx not found, using a private environment at $PREFIX"
  rm -rf "$PREFIX"
  python3 -m venv "$PREFIX"
  "$PREFIX/bin/pip" install --quiet --upgrade pip >/dev/null
  "$PREFIX/bin/pip" install --quiet "$REPO"
  mkdir -p "$BINDIR"
  ln -sf "$PREFIX/bin/trueredact" "$BIN"
  TARGET="$BIN"
fi

[ -x "$TARGET" ] || { echo "error: installed but $TARGET is missing" >&2; exit 1; }

mkdir -p "$APPS" "$ICONS"
install -m 644 "$REPO/packaging/trueredact.svg" "$ICON"

# Exec is absolute: the desktop session does not share this shell's PATH.
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=TrueRedact
GenericName=PDF Redaction Checker
Comment=Check whether a PDF still contains text hidden under a black box
Exec=$TARGET ui
Icon=trueredact
Terminal=false
Categories=Office;
Keywords=pdf;redaction;privacy;security;audit;
StartupNotify=true
EOF
chmod 644 "$DESKTOP"

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

echo
echo "Done."
echo "  Command:  trueredact scan file.pdf"
echo "  Window:   search your applications for \"TrueRedact\""
case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *) echo
     echo "Note: $BINDIR is not on your PATH."
     echo "Add it with:  echo 'export PATH=\"\$PATH:$BINDIR\"' >> ~/.bashrc" ;;
esac
echo
echo "To remove later:  ./install.sh --uninstall"
