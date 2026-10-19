"""Command line entry point: python3 -m avincecam <command> [--json]

Every command answers with one JSON object on stdout. Commands that are not
implemented yet say so explicitly instead of returning empty results.
"""

import argparse
import json
import sys

from . import SCHEMA_VERSION, __version__
from . import libcamera, merge, sysfs
from .model import CoreError, to_dict

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
    sub.add_parser("status", help="state of the own preview")
    preview = sub.add_parser("preview", help="start a preview")
    preview.add_argument("camera")
    sub.add_parser("stop", help="stop the own preview")
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
    return not_implemented(args.command)


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
        # libcamera may also list USB cameras, so allow for all of them.
        all_cameras, _ = sysfs.scan(args.sysfs_root, _ipa_dirs(args))

        def all_found(probed):
            return merge.count_matches(wanted, probed) >= len(wanted)

        try:
            probed, probe_errors, calls = libcamera.probe_cameras(
                runner, len(all_cameras), stop=all_found)
            errors.extend(probe_errors)
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
            camera.formats_source = "v4l2 probing not implemented yet"

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


def cmd_scan(args):
    cameras, unclassified = sysfs.scan(args.sysfs_root, _ipa_dirs(args))
    emit({
        "command": "scan",
        "source": "sysfs",
        "opens_devices": False,
        "cameras": [to_dict(c) for c in cameras],
        "unclassified": unclassified,
        "errors": [],
    })
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
