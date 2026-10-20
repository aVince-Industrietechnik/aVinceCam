# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Merge libcamera details into the cameras found in sysfs.

A libcamera camera is matched to a sysfs sensor by sensor model and position.
If that is not unique, by model alone. Without a unique match nothing is
merged and a warning says so – a wrong match would show the data of the
wrong camera.
"""

from . import libcamera
from .model import Format


def _candidates(camera, entries):
    by_model = [e for e in entries if e["model"] == camera.model]
    if camera.location:
        exact = [e for e in by_model if e["location"] == camera.location]
        if exact:
            return exact
    return by_model


def _entries(probed):
    entries = []
    for index, info, _raw in probed:
        if info is None:
            entries.append({"index": index, "info": None, "model": None,
                            "location": None})
        else:
            entries.append({"index": index, "info": info,
                            "model": libcamera.model(info),
                            "location": libcamera.location(info)})
    return entries


def count_matches(cameras, probed):
    """How many of the given cameras already have exactly one match."""
    entries = [e for e in _entries(probed) if e["info"] is not None]
    return sum(1 for c in cameras if len(_candidates(c, entries)) == 1)


def apply_libcamera(cameras, probed):
    """Fill formats, frame rates and backend ids of libcamera cameras in place.

    `probed` is the first result of libcamera.probe_cameras().
    Returns a list of warnings that concern no single camera.
    """
    entries = _entries(probed)
    used = set()
    notes = []
    if any(e["info"] is None for e in entries):
        notes.append("cam-output-unparsed")

    for camera in cameras:
        if camera.api != "libcamera":
            continue
        matches = [e for e in _candidates(camera, entries)
                   if e["info"] is not None and e["index"] not in used]
        if len(matches) != 1:
            camera.formats_source = "no unique libcamera match"
            camera.warnings.append("libcamera-no-match")
            continue
        entry = matches[0]
        used.add(entry["index"])
        info = entry["info"]

        camera.backend_id = info["id"]
        camera.sensor_size = libcamera.sensor_size(info)
        camera.frame_rate_limits = libcamera.frame_rate_limits(info)
        fmts = libcamera.formats(info)
        if fmts:
            camera.formats = [Format(pixel_format=f, width=w, height=h)
                              for f, w, h in fmts]
            camera.formats_source = "libcamera cam (default stream)"
        else:
            camera.formats_source = "unparsed"
        reported = libcamera.location(info)
        if camera.location and reported and reported != camera.location:
            camera.warnings.append("location-mismatch")
        elif not camera.location and reported:
            camera.location = reported
    return notes
