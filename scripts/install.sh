#!/bin/bash
# Install aVinceCam for the current user (no root rights, no autostart).
#
#   scripts/install.sh
#
# Copies the applet together with its core to
#   ~/.local/share/cinnamon/applets/avincecam@avince/
# and the translations to ~/.local/share/locale/<lang>/LC_MESSAGES/.
# Anything already there is backed up first to
#   ~/.local/share/avincecam/backups/<time>/
# Every installed path is listed in ~/.local/share/avincecam/installed.txt;
# scripts/uninstall.sh removes exactly those paths.
#
# The applet is NOT added to the panel: do that via
# System Settings -> Applets. Cinnamon loads new applet code only after a
# restart (Alt+F2, r, Enter on X11).

set -eu

UUID="avincecam@avince"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$REPO/applet/$UUID"
DATA="$HOME/.local/share"
TARGET="$DATA/cinnamon/applets/$UUID"
STATE="$DATA/avincecam"
MANIFEST="$STATE/installed.txt"
STAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
BACKUP="$STATE/backups/$STAMP"

say() { printf '%s\n' "$*"; }

[ -f "$SOURCE/metadata.json" ] || { say "applet not found in $SOURCE" >&2; exit 2; }

if [ -L "$TARGET" ]; then
    say "refused: $TARGET is a development link." >&2
    say "Remove it first with scripts/dev-link.sh unlink." >&2
    exit 1
fi

mkdir -p "$STATE" "$DATA/cinnamon/applets"

backup() {  # backup <path>
    local path="$1"
    [ -e "$path" ] || return 0
    local rel="${path#"$HOME"/}"
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp -a -- "$path" "$BACKUP/$rel"
    say "backed up: $path"
}

# 1. applet with its core, in a new folder that replaces the old one at once
backup "$TARGET"
NEW="$TARGET.new-$STAMP"
mkdir -p "$NEW/core"
(cd "$SOURCE" && tar --exclude='__pycache__' -cf - .) | (cd "$NEW" && tar -xf -)
cp -a -- "$REPO/core/avincecam-core" "$NEW/core/"
(cd "$REPO/core" && tar --exclude='__pycache__' -cf - avincecam) | (cd "$NEW/core" && tar -xf -)
if [ -e "$TARGET" ]; then
    rm -rf -- "${TARGET:?}"
fi
mv -- "$NEW" "$TARGET"
installed=("$TARGET")

# 2. translations (compiled from po/*.po)
if command -v msgfmt >/dev/null 2>&1; then
    for po in "$SOURCE"/po/*.po; do
        [ -e "$po" ] || continue
        lang="$(basename "$po" .po)"
        mo="$DATA/locale/$lang/LC_MESSAGES/$UUID.mo"
        backup "$mo"
        mkdir -p "$(dirname "$mo")"
        msgfmt -c -o "$mo" "$po"
        installed+=("$mo")
    done
else
    say "note: msgfmt not found, translations not installed (package gettext)"
fi

# 3. list of installed paths for uninstall.sh
{
    printf '# aVinceCam installed %s from %s\n' "$STAMP" "$(git -C "$REPO" log --oneline -1 2>/dev/null || echo "?")"
    printf '%s\n' "${installed[@]}"
} > "$MANIFEST"

say "installed: $TARGET"
say "Add the applet via System Settings -> Applets; restart Cinnamon (Alt+F2, r, Enter) after an update."
