# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Still photo of a libcamera camera, saved as PNG (decision D13).

`cam` streams about 30 frames so that exposure and the ImgU's temporal noise
reduction have settled; only the last frame is kept. Measured on an Intel
IPU3 laptop: with 10 frames the exposure still jumps (analogue gain 1x to
33x) and the frames come out smeared; after ~30 frames the picture is stable.

The frames are written to the runtime folder (tmpfs) and all but the last
are deleted right away. The PNG is written with the standard library only.
"""

import os
import re
import shutil
import struct
import subprocess
import time
import zlib

DEFAULT_SIZE = (1600, 1200)
DEFAULT_FRAMES = 30
SUBFOLDER = "aVinceCam"
_FRAME = re.compile(r"^frame-cam0-stream0-(\d+)\.bin$")
_EXPOSURE = re.compile(r"\bExposureTime = (\d+)")
_GAIN = re.compile(r"\bAnalogueGain = ([0-9.]+)")


class SnapshotError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def cam_args(camera_id, size, frames, frame_dir):
    """Arguments for `cam`: one still stream, NV12, frames written to frame_dir."""
    width, height = size
    return ["-c", camera_id,
            "--stream", f"role=still,width={width},height={height},pixelformat=NV12",
            f"--capture={frames}", "--metadata",
            "--file=" + os.path.join(frame_dir, "frame-#.bin")]


def keep_last_frame(frame_dir):
    """Delete every frame but the one with the highest sequence number.

    Returns the path of the kept frame, or None if there is none.
    """
    frames = []
    for name in os.listdir(frame_dir):
        match = _FRAME.match(name)
        if match:
            frames.append((int(match.group(1)), os.path.join(frame_dir, name)))
        else:
            os.remove(os.path.join(frame_dir, name))
    frames.sort()
    for _seq, path in frames[:-1]:
        os.remove(path)
    return frames[-1][1] if frames else None


def last_metadata(text):
    """Exposure time (µs) and analogue gain of the last frame cam reported."""
    exposure = _EXPOSURE.findall(text)
    gain = _GAIN.findall(text)
    return {"exposure_us": int(exposure[-1]) if exposure else None,
            "analogue_gain": round(float(gain[-1]), 2) if gain else None}


# --------------------------------------------------------------- NV12 -> PNG

def _tables():
    """BT.601 limited range, integer arithmetic as in most video code."""
    ty = [298 * (y - 16) + 128 for y in range(256)]
    rv = [409 * (v - 128) for v in range(256)]
    gu = [-100 * (u - 128) for u in range(256)]
    gv = [-208 * (v - 128) for v in range(256)]
    bu = [516 * (u - 128) for u in range(256)]
    # clamp(x >> 8) for every possible sum, shifted so the index is never negative
    low, high = ty[0] + rv[0] + gu[255] + gv[255] + bu[0], ty[255] + rv[255] + bu[255]
    clamp = bytes(max(0, min(255, v >> 8)) for v in range(low, high + 1))
    return ty, rv, gu, gv, bu, clamp, low


def nv12_rows(data, width, height):
    """RGB rows (bytes) of an NV12 frame; the row stride is taken from the size."""
    stride = len(data) * 2 // (3 * height)
    if stride < width or stride * height * 3 // 2 > len(data):
        raise SnapshotError("frame-size", "the frame does not match the requested size",
                            f"{len(data)} bytes for {width}x{height}")
    ty, rv, gu, gv, bu, clamp, low = _tables()
    uv_base = stride * height
    for r in range(height):
        luma = data[r * stride:r * stride + width]
        chroma = data[uv_base + (r // 2) * stride:uv_base + (r // 2) * stride + width]
        row = bytearray(width * 3)
        i = 0
        for x in range(0, width, 2):
            u, v = chroma[x], chroma[x + 1]
            dr, dg, db = rv[v] - low, gu[u] + gv[v] - low, bu[u] - low
            for y in (luma[x], luma[x + 1]):
                t = ty[y]
                row[i] = clamp[t + dr]
                row[i + 1] = clamp[t + dg]
                row[i + 2] = clamp[t + db]
                i += 3
        yield bytes(row)


def write_png(path, width, height, rows):
    """8-bit RGB PNG; written to a temporary name and then renamed."""
    compressor = zlib.compressobj(6)
    parts = []
    for row in rows:
        parts.append(compressor.compress(b"\0" + row))
    parts.append(compressor.flush())
    idat = b"".join(parts)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        handle.write(chunk(b"IDAT", idat))
        handle.write(chunk(b"IEND", b""))
    os.replace(tmp, path)


# --------------------------------------------------------------- where to save

def pictures_dir():
    """The user's pictures folder (xdg-user-dir), or ~/Pictures."""
    home = os.path.expanduser("~")
    tool = shutil.which("xdg-user-dir")
    if tool:
        try:
            out = subprocess.run([tool, "PICTURES"], capture_output=True, text=True,
                                 timeout=5, stdin=subprocess.DEVNULL).stdout.strip()
            if out and out != home and os.path.isabs(out):
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.join(home, "Pictures")


def photo_path(directory, now=None):
    """aVinceCam_YYYY-MM-DD_HH-MM-SS.png in `directory`, never overwriting a file."""
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now))
    path = os.path.join(directory, f"aVinceCam_{stamp}.png")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"aVinceCam_{stamp}-{n}.png")
        n += 1
    return path
