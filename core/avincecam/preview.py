"""Start and stop the own preview.

Rules (see docs/decisions.md):
* Only one aVinceCam preview at a time. On Intel IPU3 two camera pipelines
  must never run together.
* The preview is started only if no visible process holds the camera.
* Only the own preview is ever stopped: the lock file stores PID and the
  process start time, and both must still match. Nothing is stopped by name.

The core writes the lock file and then replaces itself with the viewer
(exec), so the PID in the lock file is the viewer's PID.
"""

import json
import os
import shutil
import signal
import time

LOCK_NAME = "preview.json"
LOG_NAME = "preview.log"
STOP_WAIT_SECONDS = 5


class PreviewError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        raise PreviewError("no-runtime-dir", "XDG_RUNTIME_DIR is not available")
    path = os.path.join(base, "avincecam")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _stat(pid, proc_root):
    """(state, start time in clock ticks) of a process, or (None, None)."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8") as handle:
            data = handle.read()
    except OSError:
        return None, None
    # The command name (field 2) may contain spaces; fields after ")" are fixed.
    fields = data[data.rfind(")") + 2:].split()
    try:
        return fields[0], int(fields[19])  # fields 3 and 22 of /proc/<pid>/stat
    except (IndexError, ValueError):
        return None, None


def start_time(pid, proc_root="/proc"):
    """Start time of a process in clock ticks since boot, or None."""
    return _stat(pid, proc_root)[1]


def read_lock(directory):
    try:
        with open(os.path.join(directory, LOCK_NAME), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def lock_alive(lock, proc_root="/proc"):
    """True if the process in the lock is still the one we started."""
    if not lock or not isinstance(lock.get("pid"), int):
        return False
    state, started = _stat(lock["pid"], proc_root)
    # "Z": the viewer has ended and only waits to be collected by its parent.
    return state not in (None, "Z", "X") and started == lock.get("start_time")


def write_lock(directory, lock):
    path = os.path.join(directory, LOCK_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(lock, handle)
    os.replace(tmp, path)


def remove_lock(directory):
    try:
        os.remove(os.path.join(directory, LOCK_NAME))
    except FileNotFoundError:
        pass


def status(directory, proc_root="/proc"):
    lock = read_lock(directory)
    if lock is None:
        return {"running": False}
    if not lock_alive(lock, proc_root):
        return {"running": False, "stale_lock": True}
    return {"running": True, "camera": lock.get("camera"), "viewer": lock.get("viewer"),
            "pid": lock.get("pid"), "log": os.path.join(directory, LOG_NAME)}


def viewer_command(camera, viewer, size):
    """Argument list of the preview program, or raise PreviewError."""
    if camera.api == "libcamera":
        if not camera.backend_id:
            raise PreviewError("no-camera-id", "the libcamera id of this camera is unknown")
        if viewer == "gst":
            width, height = size
            return ["gst-launch-1.0", "-q", "libcamerasrc",
                    "camera-name=" + camera.backend_id, "!",
                    f"video/x-raw,width={width},height={height}", "!",
                    "videoconvert", "!", "autovideosink"]
        return ["qcam", "-c", camera.backend_id]
    if camera.api == "v4l2":
        capture = next((n for n in camera.nodes if os.path.basename(n).startswith("video")), None)
        if not capture:
            raise PreviewError("no-capture-node", "no video node for this camera")
        return ["gst-launch-1.0", "-q", "v4l2src", "device=" + capture, "!",
                "videoconvert", "!", "autovideosink"]
    raise PreviewError("unsupported-camera", f"no preview for api {camera.api!r}")


def check_tool(argv):
    if not shutil.which(argv[0]):
        package = {"qcam": "libcamera-tools", "gst-launch-1.0": "gstreamer1.0-tools"}.get(argv[0])
        raise PreviewError("viewer-missing", f"the preview program {argv[0]} is not installed",
                           package)


def stop(directory, proc_root="/proc", wait=STOP_WAIT_SECONDS):
    """Stop the own preview. Returns a result dict; never touches other processes."""
    lock = read_lock(directory)
    if lock is None:
        return {"stopped": False, "reason": "no-preview"}
    if not lock_alive(lock, proc_root):
        remove_lock(directory)
        return {"stopped": False, "reason": "already-ended"}
    pid = lock["pid"]
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not lock_alive(lock, proc_root):
            remove_lock(directory)
            return {"stopped": True, "pid": pid, "forced": False}
        time.sleep(0.1)
    # Still the same process after the grace period: our own viewer hangs.
    if lock_alive(lock, proc_root):
        os.kill(pid, signal.SIGKILL)
    remove_lock(directory)
    return {"stopped": True, "pid": pid, "forced": True}
