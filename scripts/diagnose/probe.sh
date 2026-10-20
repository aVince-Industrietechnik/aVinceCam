#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
# Run ONE diagnostic command and record what it did to the kernel log.
#
# This wrapper does not decide what is safe. Every command run through it
# must be approved beforehand. It only adds a timeout, captures the output
# and shows the camera related kernel messages that appeared meanwhile.
#
# Usage: probe.sh OUTPUT_DIR NAME -- COMMAND [ARGS...]
#   e.g. probe.sh /some/dir cam-list -- cam --list

set -u

OUT="${1:?usage: probe.sh OUTPUT_DIR NAME -- COMMAND...}"
NAME="${2:?usage: probe.sh OUTPUT_DIR NAME -- COMMAND...}"
shift 2
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "no command given" >&2; exit 2; }

# Hard guard: sub-devices (sensor, lens/VCM) are never addressed directly.
for arg in "$@"; do
    case "$arg" in
        *v4l-subdev*)
            echo "refused: direct access to sub-devices is not allowed ($arg)" >&2
            exit 2 ;;
    esac
done

PATTERN='dw9714|ov5670|ov8858|ipu3|cio2|imgu|tps68470|int3472|INT347|uvcvideo|libcamera|i2c.*(fail|error|timeout)'
TIMEOUT="${PROBE_TIMEOUT:-20}"

mkdir -p "$OUT" || exit 1
STAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
FILE="$OUT/${STAMP}_${NAME}.txt"
START_EPOCH="$(date +%s)"

{
    echo "# probe: $NAME  $(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "command: $*"
    echo "timeout: ${TIMEOUT}s"
    echo
    echo "## Output"
    T0="$(date +%s.%N)"
    timeout --kill-after=5 "$TIMEOUT" "$@" 2>&1
    RC=$?
    T1="$(date +%s.%N)"
    echo
    echo "## Result"
    echo "exit code: $RC  (124 = timeout)"
    echo "duration:  $(LC_ALL=C awk -v a="$T0" -v b="$T1" 'BEGIN { printf "%.2f", b - a }') s"
    sleep 2
    echo
    echo "## Camera related kernel messages since start"
    NEW="$(journalctl -k --no-pager -q -o short-iso --since "@$START_EPOCH" 2>&1 | grep -Ei "$PATTERN")"
    if [ -n "$NEW" ]; then
        echo "count: $(printf '%s\n' "$NEW" | grep -c .)"
        printf '%s\n' "$NEW"
    else
        echo "count: 0"
    fi
} > "$FILE" 2>&1

cat "$FILE"
echo
echo "saved: $FILE"
