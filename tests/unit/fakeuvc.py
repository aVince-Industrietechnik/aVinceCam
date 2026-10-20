# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""A simulated USB webcam for uvcphoto: answers the V4L2 ioctls, memory maps
and waits of a photo without any hardware (formats of a recorded webcam)."""

import errno
import json
import os

from avincecam import uvcphoto, v4l2

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "uvc", "realtek-0bda-5697.json")


def recorded_formats():
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)["formats"]


def fake_jpeg(width=16, height=8, with_dht=False, trailer=b""):
    """A structurally valid baseline JPEG header with dummy scan data."""
    def seg(marker, body):
        return b"\xff" + bytes([marker]) + (len(body) + 2).to_bytes(2, "big") + body
    out = b"\xff\xd8"
    out += seg(0xDB, b"\x00" + bytes(range(1, 65)))
    out += seg(0xC0, b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") +
               b"\x03\x01\x21\x00\x02\x11\x01\x03\x11\x01")
    if with_dht:
        out += uvcphoto.STANDARD_DHT
    out += seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    return out + b"\x12\x34\x56\x78" + b"\xff\xd9" + trailer


def yuyv_frame(width, height, y=235, u=128, v=128):
    return bytes([y, u, y, v]) * (width * height // 2)


class _Map(bytearray):
    closed = False

    def close(self):
        self.closed = True


class FakeUvc:
    """Backend for uvcphoto with one capture node (the first video node)."""

    def __init__(self, formats=None, frame=None, capture_node="video0", refuse_format=False,
                 empty_frames=0, no_frames=False):
        self.formats = recorded_formats() if formats is None else formats
        self.frame = frame
        self.capture_node = capture_node
        self.refuse_format = refuse_format
        self.empty_frames = empty_frames
        self.no_frames = no_frames
        self.opened, self.calls, self.maps, self.queue = [], [], [], []
        self.open_fds = set()
        self.mode = None
        self.time = 0.0
        self.dequeued = 0

    # ------------------------------------------------------------ system calls
    def open(self, path):
        self.opened.append(path)
        fd = 100 + len(self.opened)
        self.open_fds.add(fd)
        self._paths = getattr(self, "_paths", {})
        self._paths[fd] = path
        return fd

    def close(self, fd):
        self.open_fds.discard(fd)

    def map(self, fd, length, offset):
        m = _Map(length)
        self.maps.append(m)
        return m

    def wait(self, fd, seconds):
        self.time += 0.05
        return not self.no_frames

    def clock(self):
        return self.time

    def ioctl(self, fd, request, arg):
        self.calls.append(request)
        node = os.path.basename(self._paths[fd])
        if request == v4l2.VIDIOC_QUERYCAP:
            arg.capabilities = v4l2.V4L2_CAP_DEVICE_CAPS | 0x1 | uvcphoto.V4L2_CAP_STREAMING
            arg.device_caps = (0x1 | uvcphoto.V4L2_CAP_STREAMING
                               if node == self.capture_node else 0x00800000)
            arg.card = b"HD Webcam"
            return 0
        if request == v4l2.VIDIOC_ENUM_FMT:
            pixes = sorted({f["pixel_format"] for f in self.formats},
                           key=lambda p: [f["pixel_format"] for f in self.formats].index(p))
            if arg.index >= len(pixes):
                raise OSError(errno.EINVAL, "end")
            arg.pixelformat = uvcphoto.fourcc_value(pixes[arg.index])
            arg.description = pixes[arg.index].encode()
            return 0
        if request == v4l2.VIDIOC_ENUM_FRAMESIZES:
            sizes = [(f["width"], f["height"]) for f in self.formats
                     if uvcphoto.fourcc_value(f["pixel_format"]) == arg.pixel_format]
            if arg.index >= len(sizes):
                raise OSError(errno.EINVAL, "end")
            arg.type = v4l2.V4L2_FRMSIZE_TYPE_DISCRETE
            arg.u.discrete.width, arg.u.discrete.height = sizes[arg.index]
            return 0
        if request == v4l2.VIDIOC_ENUM_FRAMEINTERVALS:
            raise OSError(errno.EINVAL, "end")
        if request == uvcphoto.VIDIOC_S_FMT:
            pix = arg.fmt.pix
            if self.refuse_format:
                pix.pixelformat = uvcphoto.fourcc_value("H264")
            pix.bytesperline = pix.width * 2 if v4l2.fourcc(pix.pixelformat) == "YUYV" else 0
            self.mode = (v4l2.fourcc(pix.pixelformat), pix.width, pix.height)
            return 0
        if request == uvcphoto.VIDIOC_REQBUFS:
            arg.count = min(arg.count, 4)
            return 0
        if request == uvcphoto.VIDIOC_QUERYBUF:
            arg.length = len(self._frame()) + 64
            arg.m.offset = arg.index * 4096
            return 0
        if request == uvcphoto.VIDIOC_QBUF:
            self.queue.append(arg.index)
            return 0
        if request == uvcphoto.VIDIOC_DQBUF:
            if not self.queue:
                raise OSError(errno.EAGAIN, "again")
            index = self.queue.pop(0)
            data = self._frame()
            self.dequeued += 1
            arg.index = index
            if self.dequeued <= self.empty_frames:
                arg.bytesused = 0
            else:
                self.maps[index][:len(data)] = data
                arg.bytesused = len(data)
            arg.sequence = self.dequeued
            return 0
        if request in (uvcphoto.VIDIOC_STREAMON, uvcphoto.VIDIOC_STREAMOFF):
            return 0
        raise OSError(errno.ENOTTY, f"unexpected ioctl {request:#x}")

    def _frame(self):
        if self.frame is not None:
            return self.frame
        pix, w, h = self.mode or ("MJPG", 16, 8)
        return yuyv_frame(w, h) if pix == "YUYV" else fake_jpeg(w, h)
