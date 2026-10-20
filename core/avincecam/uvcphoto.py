# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Still photo of a USB (UVC) camera with plain V4L2 (decision D17).

Only the camera's own video node is opened – safety.check_v4l2_query() must
allow it (UVC nodes only); libcamera is not involved. The format is set to
MJPEG (else YUYV) in the size closest to the requested one, a few memory
mapped buffers are streamed for about a second so that the camera's
automatic exposure settles, and the last frame is kept. No camera control
(brightness, exposure, …) is changed.

MJPEG frames already are JPEG pictures and are saved as they are; many
webcams leave out the Huffman tables, which are then added (standard tables
of the JPEG specification, Annex K). YUYV frames are converted to PNG.

The system calls are injectable (Backend) so that the tests can stream from
a simulated camera instead of real hardware.
"""

import ctypes
import errno
import fcntl
import mmap
import os
import select
import time

from . import safety, v4l2

SETTLE_SECONDS = 1.0
MIN_FRAMES = 10
MAX_SECONDS = 8.0
BUFFERS = 4
PREFERRED = ("MJPG", "YUYV")

# ---------------------------------------------------------------- structures


class PixFormat(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("pixelformat", ctypes.c_uint32), ("field", ctypes.c_uint32),
                ("bytesperline", ctypes.c_uint32), ("sizeimage", ctypes.c_uint32),
                ("colorspace", ctypes.c_uint32), ("priv", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("ycbcr_enc", ctypes.c_uint32),
                ("quantization", ctypes.c_uint32), ("xfer_func", ctypes.c_uint32)]


class _FormatUnion(ctypes.Union):
    # raw_data[200]; the kernel union also holds pointers (8-byte alignment)
    _fields_ = [("pix", PixFormat), ("raw_data", ctypes.c_ubyte * 200),
                ("_align", ctypes.c_uint64)]


class Format(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("fmt", _FormatUnion)]


class RequestBuffers(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("memory", ctypes.c_uint32), ("capabilities", ctypes.c_uint32),
                ("flags", ctypes.c_uint8), ("reserved", ctypes.c_uint8 * 3)]


class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _Timecode(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("frames", ctypes.c_uint8), ("seconds", ctypes.c_uint8),
                ("minutes", ctypes.c_uint8), ("hours", ctypes.c_uint8),
                ("userbits", ctypes.c_uint8 * 4)]


class _BufferM(ctypes.Union):
    _fields_ = [("offset", ctypes.c_uint32), ("userptr", ctypes.c_ulong),
                ("planes", ctypes.c_void_p), ("fd", ctypes.c_int32)]


class Buffer(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("bytesused", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("field", ctypes.c_uint32), ("timestamp", _Timeval),
                ("timecode", _Timecode), ("sequence", ctypes.c_uint32),
                ("memory", ctypes.c_uint32), ("m", _BufferM),
                ("length", ctypes.c_uint32), ("reserved2", ctypes.c_uint32),
                ("request_fd", ctypes.c_int32)]


_RW = v4l2._IOC_READ | v4l2._IOC_WRITE
VIDIOC_S_FMT = v4l2._ioc(_RW, 5, ctypes.sizeof(Format))
VIDIOC_REQBUFS = v4l2._ioc(_RW, 8, ctypes.sizeof(RequestBuffers))
VIDIOC_QUERYBUF = v4l2._ioc(_RW, 9, ctypes.sizeof(Buffer))
VIDIOC_QBUF = v4l2._ioc(_RW, 15, ctypes.sizeof(Buffer))
VIDIOC_DQBUF = v4l2._ioc(_RW, 17, ctypes.sizeof(Buffer))
VIDIOC_STREAMON = v4l2._ioc(v4l2._IOC_WRITE, 18, ctypes.sizeof(ctypes.c_int))
VIDIOC_STREAMOFF = v4l2._ioc(v4l2._IOC_WRITE, 19, ctypes.sizeof(ctypes.c_int))

V4L2_MEMORY_MMAP = 1
V4L2_FIELD_ANY = 0
V4L2_BUF_FLAG_ERROR = 0x40
V4L2_CAP_STREAMING = 0x04000000


def fourcc_value(text):
    return sum(ord(c) << (8 * i) for i, c in enumerate(text.ljust(4)[:4]))


class PhotoError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# Set by the fake core (applet tests) to a simulated camera.
TEST_BACKEND = None


class Backend:
    """The system calls used for one photo; replaced by a fake in tests."""

    def __init__(self):
        if os.environ.get("AVINCECAM_NO_HARDWARE") == "1":
            raise v4l2.HardwareBlocked("photo")

    ioctl = staticmethod(fcntl.ioctl)
    close = staticmethod(os.close)

    @staticmethod
    def open(path):
        return os.open(path, os.O_RDWR | os.O_NONBLOCK)

    @staticmethod
    def map(fd, length, offset):
        return mmap.mmap(fd, length, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                         offset=offset)

    @staticmethod
    def wait(fd, seconds):
        readable, _w, _x = select.select([fd], [], [], seconds)
        return bool(readable)

    clock = staticmethod(time.monotonic)


# ---------------------------------------------------------------- choosing the mode

def choose_mode(formats, size):
    """(pixel format, width, height) for a photo of about `size`.

    MJPEG is preferred, then YUYV. Of the sizes of that format the requested
    one wins, else the largest one not larger than requested, else the
    smallest larger one. None if the camera offers neither format.
    """
    want_w, want_h = size
    for pix in PREFERRED:
        sizes = sorted({(f["width"], f["height"]) for f in formats
                        if f["pixel_format"] == pix})
        if not sizes:
            continue
        if (want_w, want_h) in sizes:
            return pix, want_w, want_h
        smaller = [s for s in sizes if s[0] <= want_w and s[1] <= want_h]
        if smaller:
            w, h = max(smaller, key=lambda s: s[0] * s[1])
        else:
            w, h = min(sizes, key=lambda s: s[0] * s[1])
        return pix, w, h
    return None


def capture_node(nodes, sysfs_root, backend):
    """(node, formats) of the first video node of a UVC camera that captures."""
    for node in nodes:
        if not os.path.basename(node).startswith("video"):
            continue
        try:
            result = v4l2.query_device(node, sysfs_root, ioctl=backend.ioctl,
                                       opener=backend.open, closer=backend.close)
        except safety.SafetyError:
            continue
        if result is not None and result["formats"]:
            return node, result["formats"]
    raise PhotoError("no-capture-node", "the camera has no video node that captures")


# ---------------------------------------------------------------- streaming

def capture(node, sysfs_root, mode, backend, settle=SETTLE_SECONDS,
            min_frames=MIN_FRAMES, max_seconds=MAX_SECONDS):
    """Stream from `node` and return the last good frame.

    Returns {"pixel_format", "width", "height", "bytesperline", "data",
    "frames"}; raises PhotoError.
    """
    safety.check_v4l2_query(node, sysfs_root)
    pix, width, height = mode
    fd = backend.open(node)
    maps = []
    streaming = False
    try:
        fmt = Format(type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE)
        fmt.fmt.pix.width, fmt.fmt.pix.height = width, height
        fmt.fmt.pix.pixelformat = fourcc_value(pix)
        fmt.fmt.pix.field = V4L2_FIELD_ANY
        backend.ioctl(fd, VIDIOC_S_FMT, fmt)
        got = fmt.fmt.pix
        if v4l2.fourcc(got.pixelformat) != pix:
            raise PhotoError("format-refused", "the camera refused the photo format",
                             f"asked {pix}, got {v4l2.fourcc(got.pixelformat)}")

        req = RequestBuffers(count=BUFFERS, type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE,
                             memory=V4L2_MEMORY_MMAP)
        backend.ioctl(fd, VIDIOC_REQBUFS, req)
        if req.count < 1:
            raise PhotoError("no-buffers", "the camera provided no buffers")
        for index in range(req.count):
            buf = Buffer(index=index, type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE,
                         memory=V4L2_MEMORY_MMAP)
            backend.ioctl(fd, VIDIOC_QUERYBUF, buf)
            maps.append(backend.map(fd, buf.length, buf.m.offset))
            backend.ioctl(fd, VIDIOC_QBUF, buf)

        kind = ctypes.c_int(v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE)
        backend.ioctl(fd, VIDIOC_STREAMON, kind)
        streaming = True
        start = backend.clock()
        last, frames = None, 0
        while True:
            elapsed = backend.clock() - start
            if last is not None and frames >= min_frames and elapsed >= settle:
                break
            if elapsed >= max_seconds:
                if last is None:
                    raise PhotoError("no-frame", "the camera delivered no frame",
                                     f"{max_seconds:g} s")
                break
            if not backend.wait(fd, min(0.5, max_seconds - elapsed)):
                continue
            buf = Buffer(type=v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE, memory=V4L2_MEMORY_MMAP)
            try:
                backend.ioctl(fd, VIDIOC_DQBUF, buf)
            except OSError as exc:
                if exc.errno == errno.EAGAIN:
                    continue
                raise
            if buf.bytesused and not buf.flags & V4L2_BUF_FLAG_ERROR:
                last = bytes(maps[buf.index][:buf.bytesused])
                frames += 1
            backend.ioctl(fd, VIDIOC_QBUF, buf)
        return {"pixel_format": pix, "width": got.width, "height": got.height,
                "bytesperline": got.bytesperline, "data": last, "frames": frames}
    finally:
        if streaming:
            try:
                backend.ioctl(fd, VIDIOC_STREAMOFF, ctypes.c_int(v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE))
            except OSError:
                pass
        for m in maps:
            m.close()
        backend.close(fd)


# ---------------------------------------------------------------- JPEG

def _dht(table_class, table_id, bits, values):
    body = bytes([(table_class << 4) | table_id]) + bytes(bits) + bytes(values)
    return body


_DC_LUM_BITS = (0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
_DC_CHR_BITS = (0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0)
_DC_VALUES = tuple(range(12))
_AC_LUM_BITS = (0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d)
_AC_LUM_VALUES = bytes((
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
    0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0, 0x24, 0x33, 0x62, 0x72,
    0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45,
    0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3,
    0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9,
    0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4,
    0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa))
_AC_CHR_BITS = (0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77)
_AC_CHR_VALUES = bytes((
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41,
    0x51, 0x07, 0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0, 0x15, 0x62, 0x72, 0xd1,
    0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44,
    0x45, 0x46, 0x47, 0x48, 0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74,
    0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a,
    0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
    0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7,
    0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4,
    0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa))

_STANDARD_DHT_BODY = (_dht(0, 0, _DC_LUM_BITS, _DC_VALUES) +
                      _dht(1, 0, _AC_LUM_BITS, _AC_LUM_VALUES) +
                      _dht(0, 1, _DC_CHR_BITS, _DC_VALUES) +
                      _dht(1, 1, _AC_CHR_BITS, _AC_CHR_VALUES))
STANDARD_DHT = (b"\xff\xc4" + (len(_STANDARD_DHT_BODY) + 2).to_bytes(2, "big") +
                _STANDARD_DHT_BODY)


def complete_jpeg(data):
    """The frame as a complete JPEG file: standard Huffman tables are added
    when the camera left them out, anything after the end marker is cut."""
    if data[:2] != b"\xff\xd8":
        raise PhotoError("bad-frame", "the camera frame is not a JPEG picture")
    pos, has_dht = 2, False
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise PhotoError("bad-frame", "the camera frame is not a valid JPEG picture")
        marker = data[pos + 1]
        if marker == 0xFF:          # fill byte
            pos += 1
            continue
        if marker == 0xC4:
            has_dht = True
        if marker == 0xDA:          # start of scan: the header ends here
            break
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            raise PhotoError("bad-frame", "the camera frame is not a valid JPEG picture")
        pos += 2 + length
    else:
        raise PhotoError("bad-frame", "the camera frame has no picture data")
    end = data.rfind(b"\xff\xd9")
    body = data[:end + 2] if end > pos else data
    if has_dht:
        return body
    return body[:pos] + STANDARD_DHT + body[pos:]


# ---------------------------------------------------------------- YUYV

def yuyv_rows(data, width, height, bytesperline=0):
    """RGB rows (bytes) of a YUYV (YUY2) frame, BT.601 limited range."""
    from . import snapshot
    stride = bytesperline or width * 2
    if stride < width * 2 or stride * height > len(data):
        raise PhotoError("frame-size", "the frame does not match its size",
                         f"{len(data)} bytes for {width}x{height}")
    ty, rv, gu, gv, bu, clamp, low = snapshot._tables()
    for r in range(height):
        line = data[r * stride:r * stride + width * 2]
        row = bytearray(width * 3)
        i = 0
        for x in range(0, width * 2 - 3, 4):
            y0, u, y1, v = line[x], line[x + 1], line[x + 2], line[x + 3]
            dr, dg, db = rv[v] - low, gu[u] + gv[v] - low, bu[u] - low
            for y in (y0, y1):
                t = ty[y]
                row[i] = clamp[t + dr]
                row[i + 1] = clamp[t + dg]
                row[i + 2] = clamp[t + db]
                i += 3
        yield bytes(row)
