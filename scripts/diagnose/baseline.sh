#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
# Record the starting state of the camera system before any test.
# Read-only: nothing below /dev is opened, nothing is changed.
#
# Usage: baseline.sh OUTPUT_DIR

set -u

OUT="${1:?usage: baseline.sh OUTPUT_DIR}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PATTERN='dw9714|ov5670|ov8858|ipu3|cio2|imgu|tps68470|int3472|INT347|uvcvideo|libcamera|i2c.*(fail|error|timeout)'

mkdir -p "$OUT" || exit 1
STAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
FILE="$OUT/${STAMP}_T0_baseline.txt"

{
    echo "# T0 baseline  $(date '+%Y-%m-%d %H:%M:%S %z')"
    echo
    echo "## System"
    echo "host:    $(hostname)"
    echo "kernel:  $(uname -r)"
    echo "booted:  $(uptime -s)"
    echo "session: ${XDG_SESSION_TYPE:-?} ${XDG_CURRENT_DESKTOP:-?}"
    echo
    echo "## Camera related kernel modules"
    lsmod | grep -Ei 'ipu|cio2|imgu|ov[0-9]|dw97|tps68470|int3472|uvc|v4l2|videodev' || echo "(none)"
    echo
    echo "## Checksums of module configuration"
    sha256sum /etc/modprobe.d/* /etc/modules-load.d/* 2>/dev/null
    echo
    echo "## Kernel log since boot, camera related"
    LINES="$(journalctl -k -b --no-pager -q -o short-iso 2>&1 | grep -Ei "$PATTERN")"
    echo "count: $(printf '%s' "$LINES" | grep -c .)"
    printf '%s\n' "$LINES"
    echo
    echo "## Processes holding camera nodes open (own user only)"
    found=0
    for fd in /proc/[0-9]*/fd/*; do
        target="$(readlink "$fd" 2>/dev/null)" || continue
        case "$target" in
            /dev/video*|/dev/media*|/dev/v4l-subdev*)
                pid="${fd#/proc/}"; pid="${pid%%/*}"
                echo "$pid $(cat /proc/$pid/comm 2>/dev/null) -> $target"
                found=1 ;;
        esac
    done
    [ "$found" = 0 ] && echo "(none visible)"
    echo
    echo "## Passive sysfs scan"
    python3 -I "$HERE/passive_scan.py" --pretty
} > "$FILE" 2>&1

echo "$FILE"
