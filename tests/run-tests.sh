#!/bin/bash
# Run all automatic tests. They need no camera and touch no device:
# hardware is simulated with temporary sysfs trees.
#
# The complete output of every run is also written to
# ~/.cache/avincecam-tests/last-run.log (outside the repository), so the
# details of a rare failure are never lost.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"

export LANGUAGE=C
export PYTHONDONTWRITEBYTECODE=1
# The core refuses to run camera tools while this is set.
export AVINCECAM_NO_HARDWARE=1
# Never reach the live desktop session from a test.
export DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/avincecam-test"

LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/avincecam-tests"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/last-run.log"

cd "$REPO" || exit 2
{
    echo "# aVinceCam tests $(date '+%Y-%m-%d %H:%M:%S')  $(git log --oneline -1 2>/dev/null)"
    PYTHONPATH="$REPO/core:$REPO/tests/unit" python3 -m unittest discover \
        -s tests/unit -t tests/unit "$@" 2>&1
    rc=$?
    python3 -I scripts/check-public.py 2>&1 || rc=1
    echo "# exit $rc"
    exit $rc
} > >(tee "$LOG")
rc=$?
wait  # let tee finish writing
exit $rc
