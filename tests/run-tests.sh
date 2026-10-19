#!/bin/bash
# Run all automatic tests. They need no camera and touch no device:
# hardware is simulated with temporary sysfs trees.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"

export LANGUAGE=C
export PYTHONDONTWRITEBYTECODE=1
# The core refuses to run camera tools while this is set.
export AVINCECAM_NO_HARDWARE=1
# Never reach the live desktop session from a test.
export DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/avincecam-test"

cd "$REPO" || exit 2
PYTHONPATH="$REPO/core:$REPO/tests/unit" python3 -m unittest discover \
    -s tests/unit -t tests/unit "$@"
rc=$?

python3 -I scripts/check-public.py || rc=1
exit $rc
