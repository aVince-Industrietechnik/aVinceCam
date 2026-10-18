"""Command line entry point: python3 -m avincecam <command> [--json]

Every command answers with one JSON object on stdout. Commands that are not
implemented yet say so explicitly instead of returning empty results.
"""

import argparse
import json
import sys

from . import SCHEMA_VERSION, __version__
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
    sub.add_parser("scan", help="list cameras from sysfs (opens no device)")
    probe = sub.add_parser("probe", help="query formats (opens devices)")
    probe.add_argument("camera", nargs="?")
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
    return not_implemented(args.command)


if __name__ == "__main__":
    sys.exit(main())
