#!/bin/bash
# Development only: make the applet from this repository known to Cinnamon
# by a symbolic link, or remove that link again.
#
#   scripts/dev-link.sh link     create the link
#   scripts/dev-link.sh unlink   remove the link (only if it is ours)
#   scripts/dev-link.sh status   show the current state
#
# The applet is NOT added to the panel by this script. Do that yourself via
# System Settings -> Applets. Nothing outside the user's home is touched.

set -u

UUID="avincecam@avince"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$REPO/applet/$UUID"
TARGET="$HOME/.local/share/cinnamon/applets/$UUID"

is_our_link() {
    [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET")" = "$(readlink -f "$SOURCE")" ]
}

case "${1:-status}" in
    link)
        if is_our_link; then
            echo "already linked: $TARGET"
            exit 0
        fi
        if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
            echo "refused: $TARGET exists and is not a link to this repository" >&2
            exit 1
        fi
        mkdir -p "$(dirname "$TARGET")" || exit 1
        ln -s "$SOURCE" "$TARGET" || exit 1
        echo "linked: $TARGET -> $SOURCE"
        ;;
    unlink)
        if ! is_our_link; then
            echo "refused: $TARGET is not a link to this repository" >&2
            exit 1
        fi
        rm -- "$TARGET" || exit 1
        echo "removed link: $TARGET"
        ;;
    status)
        if is_our_link; then
            echo "linked: $TARGET -> $(readlink "$TARGET")"
        elif [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
            echo "something else exists at $TARGET"
        else
            echo "not linked"
        fi
        ;;
    *)
        echo "usage: $0 link|unlink|status" >&2
        exit 2
        ;;
esac
