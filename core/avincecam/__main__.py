"""Command line entry point: python3 -m avincecam <command> [--json]

Every command answers with one JSON object on stdout. Commands that are not
implemented yet say so explicitly instead of returning empty results.
"""

import argparse
import json
import os
import sys
import time

from . import SCHEMA_VERSION, __version__
from . import busy, libcamera, merge, preview, safety, sysfs, v4l2
from .model import CoreError, Format, to_dict

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_IMPLEMENTED = 3

COMMANDS = ("scan", "probe", "status", "preview", "stop", "diagnose", "version")


def emit(payload):
    payload = {"schema": SCHEMA_VERSION, **payload}
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def not_implemented(command):
    error = CoreError(code="not-implemented",
                      message=f"command '{command}' is not implemented yet")
    emit({"command": command, "errors": [to_dict(error)]})
    return EXIT_NOT_IMPLEMENTED


def build_parser():
    parser = argparse.ArgumentParser(prog="avincecam",
                                     description="aVinceCam core")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print version information")
    scan = sub.add_parser("scan", help="list cameras from sysfs (opens no device)")
    scan.add_argument("--sysfs-root", default="/sys", help=argparse.SUPPRESS)
    scan.add_argument("--ipa-dir", action="append", help=argparse.SUPPRESS)
    probe = sub.add_parser("probe", help="query formats (opens devices)")
    probe.add_argument("camera", nargs="?")
    probe.add_argument("--sysfs-root", default="/sys", help=argparse.SUPPRESS)
    probe.add_argument("--ipa-dir", action="append", help=argparse.SUPPRESS)
    # Answer from recorded cam output instead of running cam (tests).
    probe.add_argument("--tool-output", help=argparse.SUPPRESS)
    status = sub.add_parser("status", help="state of the own preview")
    preview = sub.add_parser("preview", help="start a preview (streams one camera)")
    preview.add_argument("camera")
    preview.add_argument("--viewer", choices=("qcam", "gst"), default="qcam")
    preview.add_argument("--size", default="1280x720",
                         help="picture size for the GStreamer viewer")
    preview.add_argument("--allow-lens", action="store_true",
                         help="also preview cameras with a focus motor "
                              "(libcamera then drives the motor automatically)")
    preview.add_argument("--sysfs-root", default="/sys", help=argparse.SUPPRESS)
    stop = sub.add_parser("stop", help="stop the own preview")
    stop.add_argument("--cleanup-only", action="store_true",
                      help="only remove the lock of a preview that has already ended; "
                           "never stops a process")
    # Test hooks: simulated /proc, /dev prefix and runtime directory.
    for command in (scan, preview, stop, status):
        command.add_argument("--runtime-dir", help=argparse.SUPPRESS)
        command.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    preview.add_argument("--dev-root", default="/", help=argparse.SUPPRESS)
    sub.add_parser("diagnose", help="diagnostic report")
    for name in sub.choices:
        sub.choices[name].add_argument("--json", action="store_true",
                                       help="JSON output (default)")
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    if args.command == "version":
        emit({"command": "version", "version": __version__})
        return EXIT_OK
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "probe":
        return cmd_probe(args)
    if args.command == "preview":
        return cmd_preview(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "status":
        return cmd_status(args)
    return not_implemented(args.command)


def _error(command, code, message, detail=None, **extra):
    emit({"command": command, **extra,
          "errors": [to_dict(CoreError(code=code, message=message, detail=detail))]})
    return EXIT_ERROR


def _runtime_dir(args):
    if args.runtime_dir:
        os.makedirs(args.runtime_dir, mode=0o700, exist_ok=True)
        return args.runtime_dir
    return preview.runtime_dir()


def _preview_status(args):
    try:
        return preview.status(_runtime_dir(args), args.proc_root)
    except preview.PreviewError as exc:
        return {"running": False, "error": exc.code}


def _parse_size(text):
    try:
        width, height = (int(v) for v in text.lower().split("x"))
    except ValueError:
        return None
    return (width, height) if 16 <= width <= 8192 and 16 <= height <= 8192 else None


def _pipeline_nodes(camera, sysfs_root):
    """Device nodes that must be free before this camera may stream.

    For complex pipelines (IPU3) all nodes of the pipeline count: only one
    camera may use the pipeline at a time.
    """
    nodes = set(camera.nodes)
    if camera.api == "libcamera":
        for node in sysfs.read_nodes(sysfs_root):
            if node["driver"] in sysfs.PIPELINE_DRIVERS:
                nodes.add(node["dev"])
        for media in sysfs.read_media(sysfs_root):
            if media["driver"] in sysfs.PIPELINE_DRIVERS:
                nodes.add(media["dev"])
    return sorted(nodes)


def cmd_preview(args):
    """Check everything, write the lock, then become the viewer (exec)."""
    cameras, _ = sysfs.scan(args.sysfs_root, sysfs.DEFAULT_IPA_DIRS)
    camera = next((c for c in cameras if c.id == args.camera), None)
    if camera is None:
        return _error("preview", "unknown-camera", f"no camera with id {args.camera}")
    if camera.has_lens and not args.allow_lens:
        # Measured on an Intel IPU3 laptop: while streaming, libcamera's
        # automatic focus writes to the lens motor about 48 times a second.
        return _error("preview", "lens-blocked",
                      "preview of cameras with a focus motor is locked "
                      "(libcamera would drive the motor automatically)")
    size = _parse_size(args.size)
    if size is None:
        return _error("preview", "bad-size", f"invalid size {args.size!r}")

    try:
        directory = _runtime_dir(args)
        lock = preview.read_lock(directory)
        if preview.lock_alive(lock, args.proc_root):
            return _error("preview", "preview-running",
                          "a preview is already running – stop it first",
                          detail=lock.get("camera"))
        preview.remove_lock(directory)  # stale or missing

        fake = os.environ.get("AVINCECAM_FAKE_VIEWER")
        uvc_formats = None
        if camera.api == "v4l2" and not fake:
            uvc_formats = _uvc_formats(camera, args.sysfs_root)
        argv = preview.viewer_command(camera, args.viewer, size, uvc_formats)
        viewer_name = os.path.basename(argv[0])
        if fake:
            argv = [fake] + argv  # tests: a stand-in that touches no camera
        elif os.environ.get("AVINCECAM_NO_HARDWARE") == "1":
            return _error("preview", "hardware-blocked",
                          "hardware access is switched off (AVINCECAM_NO_HARDWARE=1)")
        else:
            preview.check_tool(argv)
    except preview.PreviewError as exc:
        return _error("preview", exc.code, exc.message, exc.detail)

    devices = _pipeline_nodes(camera, args.sysfs_root)
    if args.dev_root != "/":
        devices = [os.path.join(args.dev_root, d.lstrip("/")) for d in devices]
    holding = busy.holders(devices, args.proc_root, exclude_pids=(os.getpid(),))
    if holding:
        names = ", ".join(sorted({h["name"] or str(h["pid"]) for h in holding}))
        return _error("preview", "camera-busy", "the camera is in use by another program",
                      detail=names, holders=holding)

    pid = os.getpid()
    log = os.path.join(directory, preview.LOG_NAME)
    preview.write_lock(directory, {
        "pid": pid, "start_time": preview.start_time(pid), "camera": camera.id,
        "viewer": viewer_name,
        "command": argv, "started": int(time.time())})
    emit({"command": "preview", "started": True, "camera": camera.id,
          "viewer": viewer_name, "pid": pid, "log": log, "errors": []})
    sys.stdout.flush()

    # From here on the viewer's output goes to the log file.
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        preview.remove_lock(directory)
        print(f"aVinceCam: could not start {argv[0]}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK  # not reached


def cmd_stop(args):
    try:
        if args.cleanup_only:
            result = preview.cleanup(_runtime_dir(args), args.proc_root)
        else:
            result = preview.stop(_runtime_dir(args), args.proc_root)
    except preview.PreviewError as exc:
        return _error("stop", exc.code, exc.message)
    except OSError as exc:
        return _error("stop", "stop-failed", "the preview could not be stopped", str(exc))
    emit({"command": "stop", **result, "errors": []})
    return EXIT_OK


def cmd_status(args):
    emit({"command": "status", "preview": _preview_status(args), "errors": []})
    return EXIT_OK


def _ipa_dirs(args):
    return tuple(args.ipa_dir) if args.ipa_dir else sysfs.DEFAULT_IPA_DIRS


def cmd_probe(args):
    cameras, _unclassified = sysfs.scan(args.sysfs_root, _ipa_dirs(args))
    errors = []
    if args.camera:
        cameras = [c for c in cameras if c.id == args.camera]
        if not cameras:
            errors.append(to_dict(CoreError(code="unknown-camera",
                                            message=f"no camera with id {args.camera}")))
            emit({"command": "probe", "cameras": [], "errors": errors})
            return EXIT_ERROR

    recorded = bool(args.tool_output)
    runner = (libcamera.RecordedRunner(args.tool_output) if recorded
              else libcamera.LiveRunner())
    notes = []
    calls = 0
    wanted = [c for c in cameras if c.api == "libcamera"]
    if wanted:
        try:
            probed, calls = _probe_libcamera(runner, wanted, args, errors)
            notes = merge.apply_libcamera(cameras, probed)
        except libcamera.ToolMissing:
            errors.append(to_dict(CoreError(
                code="libcamera-tools-missing",
                message="the libcamera tool 'cam' is not installed")))
        except libcamera.HardwareBlocked as exc:
            errors.append(to_dict(CoreError(
                code="hardware-blocked",
                message="hardware access is switched off (AVINCECAM_NO_HARDWARE=1)",
                detail=str(exc))))
    for camera in cameras:
        if camera.api == "v4l2":
            _probe_v4l2(camera, args.sysfs_root, errors)

    emit({
        "command": "probe",
        "source": "sysfs+libcamera" + (" (recorded)" if recorded else ""),
        "opens_devices": not recorded,
        "libcamera_calls": calls,
        "cameras": [to_dict(c) for c in cameras],
        "notes": notes,
        "errors": errors,
    })
    fatal = {"libcamera-tools-missing", "hardware-blocked"}
    return EXIT_ERROR if any(e["code"] in fatal for e in errors) else EXIT_OK


def _probe_libcamera(runner, wanted, args, errors):
    """cam output for the wanted cameras with as few cam calls as possible.

    Every call powers up all cameras of an Intel IPU3 machine. With the
    libcamera ids known from sysfs, each camera needs exactly one call.
    Otherwise, or if cam does not know an id, cam indices are tried in turn.
    Returns (probed, number of calls).
    """
    probed, calls, complete = [], 0, False
    ids = [c.backend_id for c in wanted]
    if all(ids):
        probed, probe_errors, calls, complete = libcamera.probe_by_ids(runner, ids)
        errors.extend(probe_errors)
    if complete:
        return probed, calls

    seen = {info["id"] for _key, info, _raw in probed if info}

    def new_entries(found):
        return [e for e in found if not (e[1] and e[1]["id"] in seen)]

    def all_found(found):
        return merge.count_matches(wanted, probed + new_entries(found)) >= len(wanted)

    # libcamera may also list USB cameras, so allow for all of them.
    all_cameras, _ = sysfs.scan(args.sysfs_root, _ipa_dirs(args))
    found, probe_errors, more_calls = libcamera.probe_cameras(
        runner, len(all_cameras), stop=all_found)
    errors.extend(probe_errors)
    return probed + new_entries(found), calls + more_calls


def _uvc_formats(camera, sysfs_root):
    """Formats of a UVC camera for choosing the preview mode, or None."""
    for node in camera.nodes:
        if not os.path.basename(node).startswith("video"):
            continue
        try:
            result = v4l2.query_device(node, sysfs_root)
        except (v4l2.HardwareBlocked, safety.SafetyError, OSError):
            continue
        if result is not None:
            return result["formats"]
    return None


def _probe_v4l2(camera, sysfs_root, errors):
    """Fill formats of a UVC camera from its capture node (read-only ioctls)."""
    for node in camera.nodes:
        if not os.path.basename(node).startswith("video"):
            continue
        try:
            result = v4l2.query_device(node, sysfs_root)
        except v4l2.HardwareBlocked as exc:
            errors.append(to_dict(CoreError(
                code="hardware-blocked",
                message="hardware access is switched off (AVINCECAM_NO_HARDWARE=1)",
                detail=str(exc))))
            return
        except safety.SafetyError as exc:
            errors.append(to_dict(CoreError(code="refused", message=str(exc))))
            continue
        except OSError as exc:
            errors.append(to_dict(CoreError(code="v4l2-failed",
                                            message=f"could not query {node}",
                                            detail=exc.strerror or str(exc))))
            continue
        if result is None:
            continue  # metadata node, no picture
        formats, limits = v4l2.summarize(result)
        camera.formats = [Format(pixel_format=f["pixel_format"], width=f["width"],
                                 height=f["height"], fps=f["fps"]) for f in formats]
        camera.formats_source = "V4L2 " + node + (" (ranges not listed)" if result["stepwise"] else "")
        camera.frame_rate_limits = limits
        return
    if camera.formats is None:
        camera.formats_source = "no capture node answered"


def cmd_scan(args):
    cameras, unclassified = sysfs.scan(args.sysfs_root, _ipa_dirs(args))
    emit({
        "command": "scan",
        "source": "sysfs",
        "opens_devices": False,
        "cameras": [to_dict(c) for c in cameras],
        "unclassified": unclassified,
        "preview": _preview_status(args),
        "errors": [],
    })
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
