#!/bin/bash
# Remove what scripts/install.sh installed, and nothing else.
#
#   scripts/uninstall.sh            keep the applet settings
#   scripts/uninstall.sh --purge    also remove the applet settings
#
# Remove the applet from the panel first (right-click -> Remove). A running
# aVinceCam preview is stopped before the files are removed.

set -eu

UUID="avincecam@avince"
DATA="$HOME/.local/share"
STATE="$DATA/avincecam"
MANIFEST="$STATE/installed.txt"
SETTINGS="$HOME/.config/cinnamon/spices/$UUID"
PURGE=0
PANEL_CHECK=1

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --no-panel-check) PANEL_CHECK=0 ;;   # for tests
        *) echo "usage: $0 [--purge]" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }

[ -f "$MANIFEST" ] || { say "nothing to remove: $MANIFEST not found"; exit 0; }

if [ "$PANEL_CHECK" = 1 ] && command -v gsettings >/dev/null 2>&1; then
    if gsettings get org.cinnamon enabled-applets 2>/dev/null | grep -q ":$UUID:"; then
        say "refused: aVinceCam is still in the panel." >&2
        say "Remove it first: right-click on its icon -> Remove." >&2
        exit 1
    fi
fi

# Stop a running preview with the installed core (stops only its own viewer).
CORE="$DATA/cinnamon/applets/$UUID/core/avincecam-core"
if [ -f "$CORE" ]; then
    python3 -I "$CORE" stop >/dev/null 2>&1 || true
fi

# Remove only paths listed in the manifest, and only below $HOME.
while IFS= read -r path; do
    case "$path" in
        ''|'#'*) continue ;;
        "$HOME"/*) ;;
        *) say "skipped (outside home): $path"; continue ;;
    esac
    if [ -e "$path" ] || [ -L "$path" ]; then
        rm -rf -- "${path:?}"
        say "removed: $path"
    fi
done < "$MANIFEST"
rm -f -- "${MANIFEST:?}"

if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -d "$XDG_RUNTIME_DIR/avincecam" ]; then
    rm -rf -- "${XDG_RUNTIME_DIR:?}/avincecam"
    say "removed: $XDG_RUNTIME_DIR/avincecam"
fi

if [ "$PURGE" = 1 ] && [ -d "$SETTINGS" ]; then
    rm -rf -- "${SETTINGS:?}"
    say "removed settings: $SETTINGS"
elif [ -d "$SETTINGS" ]; then
    say "kept settings: $SETTINGS (use --purge to remove them)"
fi

say "Backups of earlier installations stay in $STATE/backups."
