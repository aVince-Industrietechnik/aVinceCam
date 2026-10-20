# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Read-only V4L2 queries for USB video class (UVC) cameras.

Only enumeration ioctls are used: VIDIOC_QUERYCAP, VIDIOC_ENUM_FMT,
VIDIOC_ENUM_FRAMESIZES and VIDIOC_ENUM_FRAMEINTERVALS. No format is set, no
buffer is requested and no stream is started. Before a node is opened,
safety.check_v4l2_query() must allow it (UVC video nodes only).

The ioctl function is injectable so that the tests can answer with
simulated devices instead of real hardware.
"""

import ctypes
import errno
import fcntl
import os

from . import safety

# ---------------------------------------------------------------- ioctl numbers

_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("V") << 8) | nr


class Capability(ctypes.Structure):
    _fields_ = [("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32),
                ("bus_info", ctypes.c_char * 32), ("version", ctypes.c_uint32),
                ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 3)]


class FmtDesc(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("description", ctypes.c_char * 32),
                ("pixelformat", ctypes.c_uint32), ("mbus_code", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 3)]


class _Discrete(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32)]


class _Stepwise(ctypes.Structure):
    _fields_ = [("min_width", ctypes.c_uint32), ("max_width", ctypes.c_uint32),
                ("step_width", ctypes.c_uint32), ("min_height", ctypes.c_uint32),
                ("max_height", ctypes.c_uint32), ("step_height", ctypes.c_uint32)]


class _SizeUnion(ctypes.Union):
    _fields_ = [("discrete", _Discrete), ("stepwise", _Stepwise)]


class FrmSizeEnum(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("pixel_format", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("u", _SizeUnion),
                ("reserved", ctypes.c_uint32 * 2)]


class Fract(ctypes.Structure):
    _fields_ = [("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32)]


class _IvalStepwise(ctypes.Structure):
    _fields_ = [("min", Fract), ("max", Fract), ("step", Fract)]


class _IvalUnion(ctypes.Union):
    _fields_ = [("discrete", Fract), ("stepwise", _IvalStepwise)]


class FrmIvalEnum(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("pixel_format", ctypes.c_uint32),
                ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("u", _IvalUnion),
                ("reserved", ctypes.c_uint32 * 2)]


VIDIOC_QUERYCAP = _ioc(_IOC_READ, 0, ctypes.sizeof(Capability))
VIDIOC_ENUM_FMT = _ioc(_IOC_READ | _IOC_WRITE, 2, ctypes.sizeof(FmtDesc))
VIDIOC_ENUM_FRAMESIZES = _ioc(_IOC_READ | _IOC_WRITE, 74, ctypes.sizeof(FrmSizeEnum))
VIDIOC_ENUM_FRAMEINTERVALS = _ioc(_IOC_READ | _IOC_WRITE, 75, ctypes.sizeof(FrmIvalEnum))

V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_DEVICE_CAPS = 0x80000000
V4L2_FRMSIZE_TYPE_DISCRETE = 1
V4L2_FRMIVAL_TYPE_DISCRETE = 1

# Safety net: a camera never offers thousands of entries.
MAX_ENTRIES = 256


class HardwareBlocked(Exception):
    """Hardware access is switched off (AVINCECAM_NO_HARDWARE=1, used by tests)."""


def fourcc(value):
    return "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4)).strip()


def _enumerate(ioctl, fd, request, make):
    """Call an ENUM ioctl with index 0, 1, ... until the driver says EINVAL."""
    for index in range(MAX_ENTRIES):
        struct = make(index)
        try:
            ioctl(fd, request, struct)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                return
            raise
        yield struct


def _open(path):
    return os.open(path, os.O_RDWR | os.O_NONBLOCK)


def query_device(dev_path, sysfs_root="/sys", ioctl=None, opener=_open, closer=os.close):
    """Capture formats of one UVC node.

    Returns None for nodes without video capture (e.g. metadata nodes), else
    {"card": str, "formats": [{"pixel_format", "description", "width",
    "height", "fps": [..]}], "stepwise": bool}.
    """
    safety.check_v4l2_query(dev_path, sysfs_root)
    if ioctl is None:
        if os.environ.get("AVINCECAM_NO_HARDWARE") == "1":
            raise HardwareBlocked(dev_path)
        ioctl = fcntl.ioctl

    fd = opener(dev_path)
    try:
        cap = Capability()
        ioctl(fd, VIDIOC_QUERYCAP, cap)
        caps = cap.device_caps if cap.capabilities & V4L2_CAP_DEVICE_CAPS else cap.capabilities
        if not caps & V4L2_CAP_VIDEO_CAPTURE:
            return None

        formats = []
        stepwise = False

        def fmt(i):
            return FmtDesc(index=i, type=V4L2_BUF_TYPE_VIDEO_CAPTURE)

        for desc in _enumerate(ioctl, fd, VIDIOC_ENUM_FMT, fmt):
            pix = desc.pixelformat

            def size(i, pix=pix):
                return FrmSizeEnum(index=i, pixel_format=pix)

            for fs in _enumerate(ioctl, fd, VIDIOC_ENUM_FRAMESIZES, size):
                if fs.type != V4L2_FRMSIZE_TYPE_DISCRETE:
                    stepwise = True  # continuous/stepwise ranges are not listed
                    break
                w, h = fs.u.discrete.width, fs.u.discrete.height

                def ival(i, pix=pix, w=w, h=h):
                    return FrmIvalEnum(index=i, pixel_format=pix, width=w, height=h)

                rates = []
                for iv in _enumerate(ioctl, fd, VIDIOC_ENUM_FRAMEINTERVALS, ival):
                    if iv.type != V4L2_FRMIVAL_TYPE_DISCRETE:
                        break
                    f = iv.u.discrete
                    if f.numerator:
                        rates.append(round(f.denominator / f.numerator, 2))
                formats.append({"pixel_format": fourcc(pix),
                                "description": desc.description.decode("utf-8", "replace"),
                                "width": w, "height": h, "fps": sorted(set(rates))})
        return {"card": cap.card.decode("utf-8", "replace"), "formats": formats,
                "stepwise": stepwise}
    finally:
        closer(fd)


def summarize(result):
    """Formats for the camera model plus overall frame rate limits."""
    rates = [r for f in result["formats"] for r in f["fps"]]
    limits = ({"min": min(rates), "max": max(rates), "source": "V4L2 frame intervals"}
              if rates else None)
    return result["formats"], limits
