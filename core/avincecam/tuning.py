# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""libcamera tuning files without the autofocus algorithm (decision D16).

On Intel IPU3, libcamera's autofocus algorithm sweeps the lens on every
stream and ignores the AfMode control; on a machine whose lens motor does
not answer, that means dozens of failed I2C writes per second (desk test
T5-2). The IPA sends the focus position with every frame, but the kernel
only writes to the motor when the value changes – so without the
algorithm, nothing is written while streaming.

libcamera looks for the tuning file in LIBCAMERA_IPA_CONFIG_PATH first
(<dir>/<ipa>/<sensor model>.yaml). aVinceCam writes a copy of the tuning
file libcamera would use, minus the "Af" entry, to its runtime folder and
sets that variable for the processes it starts. The system's files are not
changed, and any calibration in them is kept.
"""

import os
import re

ENV = "LIBCAMERA_IPA_CONFIG_PATH"
SYSTEM_DIRS = ("/etc/libcamera/ipa", "/usr/share/libcamera/ipa",
               "/usr/local/share/libcamera/ipa")
# libcamera pipeline -> IPA module name
IPA_NAMES = {"ipu3": "ipu3"}
FALLBACK = "uncalibrated.yaml"
# Test hook: folders to use instead of SYSTEM_DIRS (colon separated).
SYSTEM_DIRS_ENV = "AVINCECAM_IPA_SYSTEM_DIRS"
_ITEM = re.compile(r"^(\s*)-\s*(\w+)\s*:")


def strip_af(text):
    """The YAML text without the "- Af:" list item (and its parameters)."""
    out = []
    skip_indent = None
    for line in text.splitlines(keepends=True):
        if skip_indent is not None:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and not stripped.startswith("#") and indent <= skip_indent:
                skip_indent = None
            else:
                continue
        match = _ITEM.match(line)
        if match and match.group(2) == "Af":
            skip_indent = len(match.group(1))
            continue
        out.append(line)
    return "".join(out)


def _system_dirs(system_dirs):
    override = os.environ.get(SYSTEM_DIRS_ENV)
    return tuple(d for d in override.split(":") if d) if override else system_dirs


def source_file(ipa, model, system_dirs=SYSTEM_DIRS):
    """The tuning file libcamera would load for this sensor, or None."""
    system_dirs = _system_dirs(system_dirs)
    for name in (f"{model}.yaml", FALLBACK):
        for directory in system_dirs:
            path = os.path.join(directory, ipa, name)
            if os.path.isfile(path):
                return path
    return None


def prepare(runtime_dir, cameras, system_dirs=SYSTEM_DIRS):
    """Write tuning files without autofocus for cameras with a lens motor.

    Returns the config folder to put into LIBCAMERA_IPA_CONFIG_PATH, or None
    if no camera needs one.
    """
    base = os.path.join(runtime_dir, "ipa-config")
    written = False
    for camera in cameras:
        ipa = IPA_NAMES.get(camera.pipeline or "")
        if not (camera.has_lens and camera.api == "libcamera" and ipa and camera.model):
            continue
        source = source_file(ipa, camera.model, system_dirs)
        if source is None:
            continue
        with open(source, encoding="utf-8") as handle:
            text = strip_af(handle.read())
        folder = os.path.join(base, ipa)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        target = os.path.join(folder, f"{camera.model}.yaml")
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write("# Written by aVinceCam: copy of " + source +
                         " without the autofocus algorithm (Af).\n" + text)
        os.replace(tmp, target)
        written = True
    return base if written else None


def environment(runtime_dir, cameras, system_dirs=SYSTEM_DIRS):
    """{LIBCAMERA_IPA_CONFIG_PATH: folder} for the given cameras, or {}."""
    folder = prepare(runtime_dir, cameras, system_dirs)
    if folder is None:
        return {}
    existing = os.environ.get(ENV)
    return {ENV: folder + (":" + existing if existing else "")}
