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
from fractions import Fraction

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


def choose_uvc_mode(formats, size):
    """Pick (pixel_format, width, height, fps) for a UVC preview, or None.

    Preference: the requested size in MJPG (compressed, full frame rate over
    USB 2.0), then the requested size in YUYV, then the largest size not
    above the request in MJPG/YUYV. Always the highest frame rate offered.
    """
    width, height = size
    usable = [f for f in formats or [] if f["pixel_format"] in ("MJPG", "YUYV") and f["fps"]]
    if not usable:
        return None

    def best(candidates):
        candidates = sorted(candidates, key=lambda f: (f["pixel_format"] != "MJPG", -max(f["fps"])))
        return candidates[0] if candidates else None

    exact = best(f for f in usable if (f["width"], f["height"]) == (width, height))
    if exact is None:
        smaller = [f for f in usable if f["width"] * f["height"] <= width * height]
        if smaller:
            area = max(f["width"] * f["height"] for f in smaller)
            exact = best(f for f in smaller if f["width"] * f["height"] == area)
    if exact is None:
        exact = best(usable)
    return exact["pixel_format"], exact["width"], exact["height"], max(exact["fps"])


def _gst_rate(fps):
    frac = Fraction(fps).limit_denominator(1001)
    return f"{frac.numerator}/{frac.denominator}"


def which(program):
    """shutil.which, separate so that tests can replace it."""
    return shutil.which(program)


def _vlc_command(device, mode):
    """VLC with its minimal Qt view: only this device is opened, and closing
    the window ends VLC (measured; the plain video window of cvlc does not)."""
    argv = ["vlc", "--intf", "qt", "--qt-minimal-view", "--no-qt-privacy-ask",
            "--play-and-exit", "v4l2://" + device]
    if mode is not None:
        fmt, w, h, fps = mode
        argv += [":v4l2-chroma=" + fmt, f":v4l2-width={w}", f":v4l2-height={h}",
                 f":v4l2-fps={fps:g}"]
    return argv


def viewer_command(camera, viewer, size, uvc_formats=None):
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
        mode = choose_uvc_mode(uvc_formats, size)
        if which("vlc"):
            return _vlc_command(capture, mode)
        # Fallback. Note: GStreamer's video4linux2 plugin opens every video
        # and sub-device node of the machine when it loads (see D12).
        source = ["gst-launch-1.0", "-q", "v4l2src", "device=" + capture, "!"]
        if mode is None:  # formats unknown: let GStreamer negotiate
            return source + ["videoconvert", "!", "autovideosink"]
        fmt, w, h, fps = mode
        if fmt == "MJPG":
            return source + [f"image/jpeg,width={w},height={h},framerate={_gst_rate(fps)}",
                             "!", "jpegdec", "!", "videoconvert", "!", "autovideosink"]
        return source + [f"video/x-raw,format=YUY2,width={w},height={h},framerate={_gst_rate(fps)}",
                         "!", "videoconvert", "!", "autovideosink"]
    raise PreviewError("unsupported-camera", f"no preview for api {camera.api!r}")


def check_tool(argv):
    if not shutil.which(argv[0]):
        package = {"qcam": "libcamera-tools", "gst-launch-1.0": "gstreamer1.0-tools",
                   "vlc": "vlc"}.get(argv[0])
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
