# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Camera details via the libcamera `cam` tool.

The text output of `cam` is not a stable interface. All parsing lives in
this module and is tested against recorded output (tests/fixtures). Output
that does not match is reported as unparsed instead of being guessed.

Only read-only `cam` options are used: `-I` (stream info), `-p`
(properties) and `--list-controls`. Nothing is captured and no control is set.
"""

import os
import re
import shutil
import subprocess

TOOL = "cam"
TIMEOUT = 10

# libcamera properties::Location
LOCATIONS = {"0": "front", "1": "back", "2": "external"}

_USING = re.compile(r"^Using camera (?P<id>.+) as cam\d+$")
_CONTROL = re.compile(r"^Control: (?P<name>\w+): \[(?P<min>.*?)\.\.(?P<max>.*)\]\s*$")
_PROPERTY = re.compile(r"^Property: (?P<name>\w+) = (?P<value>.*)$")
_STREAM = re.compile(r"^\d+: (?P<w>\d+)x(?P<h>\d+)-(?P<fmt>\w+)$")
_PIXELFORMAT = re.compile(r"^\s*\* Pixelformat: (?P<fmt>\w+)")
_SIZE = re.compile(r"^\s*- (?P<w>\d+)x(?P<h>\d+)$")
_LIST_ENTRY = re.compile(r"^(?P<index>\d+): (?P<name>.*) \((?P<id>.+)\)$")
_DIMENSIONS = re.compile(r"^(\d+)x(\d+)$")


class ToolMissing(Exception):
    """The cam tool is not installed."""


class HardwareBlocked(Exception):
    """Hardware access is switched off (AVINCECAM_NO_HARDWARE=1, used by tests)."""


class Result:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class LiveRunner:
    """Runs the real cam tool. Every call powers up the cameras briefly."""

    def __init__(self, tool=TOOL, timeout=TIMEOUT):
        self.tool = tool
        self.timeout = timeout

    def run(self, args):
        if os.environ.get("AVINCECAM_NO_HARDWARE") == "1":
            raise HardwareBlocked(" ".join([self.tool, *args]))
        path = shutil.which(self.tool)
        if not path:
            raise ToolMissing(self.tool)
        try:
            done = subprocess.run([path, *args], capture_output=True, text=True,
                                  timeout=self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return Result(124, "", "timeout")
        return Result(done.returncode, done.stdout, done.stderr)


class RecordedRunner:
    """Answers from recorded output files instead of running cam.

    `cam -c N` reads info-N.txt; `cam -c ID` reads the info file whose
    "Using camera ID" line names that id.
    """

    def __init__(self, directory):
        self.directory = directory

    def run(self, args):
        if args == ["--list"]:
            name = "list.txt"
        elif len(args) >= 2 and args[0] == "-c":
            name = (f"info-{args[1]}.txt" if args[1].isdigit()
                    else self._info_file_for_id(args[1]))
        else:
            return Result(2, "", "unsupported recorded command")
        if name is None:
            return Result(1, "", f"camera {args[1:2]} not found (recorded)")
        path = os.path.join(self.directory, name)
        try:
            with open(path, encoding="utf-8") as handle:
                return Result(0, handle.read())
        except OSError:
            return Result(1, "", f"camera {args[1:2]} not found (recorded)")

    def _info_file_for_id(self, camera_id):
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            return None
        for name in names:
            if not (name.startswith("info-") and name.endswith(".txt")):
                continue
            with open(os.path.join(self.directory, name), encoding="utf-8") as handle:
                match = _USING.match(handle.readline().rstrip())
            if match and match["id"] == camera_id:
                return name
        return None


def info_args(camera):
    """Arguments for one camera, given by cam index (1, 2, ...) or by id."""
    return ["-c", str(camera), "-I", "-p", "--list-controls"]


def parse_list(text):
    cameras = []
    for line in text.splitlines():
        match = _LIST_ENTRY.match(line.strip())
        if match:
            cameras.append({"index": int(match["index"]),
                            "name": match["name"], "id": match["id"]})
    return cameras


def _number(text):
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def parse_info(text):
    """Parse `cam -c N -I -p --list-controls` output.

    Returns a dict with id, controls, properties and streams, or None if the
    text does not look like cam info output at all.
    """
    info = {"id": None, "controls": {}, "properties": {}, "streams": []}
    stream = None
    pixel_format = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if match := _USING.match(line):
            info["id"] = match["id"]
        elif match := _CONTROL.match(line):
            info["controls"][match["name"]] = (match["min"].strip(), match["max"].strip())
        elif match := _PROPERTY.match(line):
            info["properties"][match["name"]] = match["value"].strip()
        elif match := _STREAM.match(line):
            stream = {"default": (int(match["w"]), int(match["h"]), match["fmt"]),
                      "formats": {}}
            info["streams"].append(stream)
        elif (match := _PIXELFORMAT.match(line)) and stream is not None:
            pixel_format = match["fmt"]
            stream["formats"].setdefault(pixel_format, [])
        elif (match := _SIZE.match(line)) and stream is not None and pixel_format:
            stream["formats"][pixel_format].append((int(match["w"]), int(match["h"])))
    if info["id"] is None and not info["properties"] and not info["streams"]:
        return None
    return info


def frame_rate_limits(info):
    """Frame rate range from FrameDurationLimits (microseconds), or None."""
    limits = info["controls"].get("FrameDurationLimits")
    if not limits:
        return None
    shortest, longest = _number(limits[0]), _number(limits[1])
    if not shortest or not longest or shortest <= 0 or longest <= 0:
        return None
    return {"min": round(1e6 / longest, 2), "max": round(1e6 / shortest, 2),
            "source": "libcamera FrameDurationLimits"}


def location(info):
    return LOCATIONS.get(info["properties"].get("Location"))


def model(info):
    return info["properties"].get("Model")


def sensor_size(info):
    match = _DIMENSIONS.match(info["properties"].get("PixelArraySize", ""))
    return [int(match[1]), int(match[2])] if match else None


def formats(info):
    """List of (pixel_format, width, height) of the first (default) stream."""
    if not info["streams"]:
        return []
    result = []
    for fmt, sizes in info["streams"][0]["formats"].items():
        result.extend((fmt, w, h) for w, h in sizes)
    return result


def probe_cameras(runner, max_cameras, stop=None):
    """Query cameras 1..max_cameras one by one.

    Every call starts libcamera, which powers up the cameras briefly, so the
    loop ends as early as possible: at the first index cam does not know, or
    as soon as `stop(found)` returns True.

    Returns (list of (index, info or None, raw stdout), errors, number of calls).
    """
    found = []
    errors = []
    calls = 0
    for index in range(1, max_cameras + 1):
        calls += 1
        result = runner.run(info_args(index))
        if result.returncode == 124:
            errors.append({"code": "timeout", "message": f"cam -c {index} timed out",
                           "detail": None})
            break
        if result.returncode != 0:
            break
        found.append((index, parse_info(result.stdout), result.stdout))
        if stop and stop(found):
            break
    return found, errors, calls


def probe_by_ids(runner, camera_ids):
    """Query each camera by its libcamera id: one call per camera.

    cam numbers cameras in the order libcamera lists them, and an external
    USB camera can come first. Asking by id needs no walk over the indices.
    Stops at the first id cam does not know, so the caller can fall back to
    probe_cameras() on machines where libcamera names cameras differently.

    Returns (list of (id, info or None, raw stdout), errors, number of calls,
    complete) – complete is False when an id was not known.
    """
    found = []
    errors = []
    calls = 0
    for camera_id in camera_ids:
        calls += 1
        result = runner.run(info_args(camera_id))
        if result.returncode == 124:
            errors.append({"code": "timeout", "message": f"cam -c {camera_id} timed out",
                           "detail": None})
            return found, errors, calls, True
        if result.returncode != 0:
            return found, errors, calls, False
        found.append((camera_id, parse_info(result.stdout), result.stdout))
    return found, errors, calls, True
