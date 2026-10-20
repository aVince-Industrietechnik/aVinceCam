# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Still photo: NV12 -> PNG, frame handling and the snapshot command with a fake cam."""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

import fakesysfs
from avincecam import preview, snapshot

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORE = os.path.join(REPO, "core", "avincecam-core")
FAKE_CAM = os.path.join(REPO, "tests", "fake-cam", "avincecam-fake-cam")
FRONT = "sensor:i2c-INT3479:00"
BACK = "sensor:i2c-INT3477:00"


def nv12(width, height, y, u, v, stride=None):
    stride = stride or width
    luma = (bytes([y]) * width + b"\0" * (stride - width)) * height
    chroma = (bytes([u, v]) * (width // 2) + b"\0" * (stride - width)) * (height // 2)
    return luma + chroma


def read_png(path):
    """(width, height, raw RGB rows) of a PNG written by snapshot.write_png."""
    with open(path, "rb") as handle:
        data = handle.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        assert crc == zlib.crc32(kind + body) & 0xffffffff, kind
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    w, h = size
    return w, h, [raw[r * (3 * w + 1) + 1:(r + 1) * (3 * w + 1)] for r in range(h)]


class ConversionTest(unittest.TestCase):
    def pixel(self, y, u, v):
        return tuple(next(snapshot.nv12_rows(nv12(2, 2, y, u, v), 2, 2))[:3])

    def test_known_colours(self):
        self.assertEqual(self.pixel(16, 128, 128), (0, 0, 0))
        self.assertEqual(self.pixel(235, 128, 128), (255, 255, 255))
        grey = self.pixel(126, 128, 128)
        self.assertTrue(all(127 <= c <= 129 for c in grey), grey)
        red = self.pixel(81, 90, 240)
        self.assertTrue(red[0] > 240 and red[1] < 10 and red[2] < 10, red)

    def test_clamped_out_of_range(self):
        self.assertEqual(self.pixel(255, 255, 255)[0], 255)
        self.assertEqual(self.pixel(0, 0, 0)[0], 0)

    def test_padded_rows(self):
        rows = list(snapshot.nv12_rows(nv12(6, 4, 235, 128, 128, stride=16), 6, 4))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], b"\xff" * 18)

    def test_short_frame_is_rejected(self):
        with self.assertRaises(snapshot.SnapshotError) as ctx:
            list(snapshot.nv12_rows(b"\0" * 10, 64, 48))
        self.assertEqual(ctx.exception.code, "frame-size")

    def test_png_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.png")
            snapshot.write_png(path, 4, 2, snapshot.nv12_rows(nv12(4, 2, 235, 128, 128), 4, 2))
            w, h, rows = read_png(path)
            self.assertEqual((w, h), (4, 2))
            self.assertEqual(rows[1], b"\xff" * 12)
            self.assertEqual(os.listdir(tmp), ["x.png"])  # no temporary file left


class HelperTest(unittest.TestCase):
    def test_cam_args(self):
        args = snapshot.cam_args(r"\_SB_.PCI0.LNK0", (1600, 1200), 30, "/run/x")
        self.assertEqual(args[:2], ["-c", r"\_SB_.PCI0.LNK0"])
        self.assertIn("role=still,width=1600,height=1200,pixelformat=NV12", args)
        self.assertIn("--capture=30", args)
        self.assertIn("--file=/run/x/frame-#.bin", args)

    def test_keep_last_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            for seq in (3, 12, 7):
                open(os.path.join(tmp, f"frame-cam0-stream0-{seq:06d}.bin"), "w").close()
            open(os.path.join(tmp, "stray.txt"), "w").close()
            kept = snapshot.keep_last_frame(tmp)
            self.assertTrue(kept.endswith("000012.bin"))
            self.assertEqual(os.listdir(tmp), [os.path.basename(kept)])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(snapshot.keep_last_frame(tmp))

    def test_last_metadata(self):
        text = "\tExposureTime = 10000\n\tAnalogueGain = 1.0\n\tExposureTime = 33018\n" \
               "\tAnalogueGain = 4.093750\n"
        self.assertEqual(snapshot.last_metadata(text),
                         {"exposure_us": 33018, "analogue_gain": 4.09})
        self.assertEqual(snapshot.last_metadata(""), {"exposure_us": None, "analogue_gain": None})

    def test_photo_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = snapshot.photo_path(tmp, now=0)
            open(first, "w").close()
            second = snapshot.photo_path(tmp, now=0)
            self.assertNotEqual(first, second)
            self.assertTrue(second.endswith("-2.png"))

    def test_pictures_dir_without_xdg_tool(self):
        with mock.patch("shutil.which", lambda name: None):
            self.assertEqual(snapshot.pictures_dir(),
                             os.path.join(os.path.expanduser("~"), "Pictures"))


class SnapshotCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = os.path.join(self.tmp.name, "sys")
        self.run_dir = os.path.join(self.tmp.name, "run")
        self.out = os.path.join(self.tmp.name, "out")
        self.dev = os.path.join(self.tmp.name, "devroot")
        os.makedirs(os.path.join(self.dev, "dev"))
        os.makedirs(self.sys)
        fakesysfs.ipu3_plus_uvc(self.sys)
        self.env = dict(os.environ, AVINCECAM_NO_HARDWARE="1", AVINCECAM_FAKE_CAM=FAKE_CAM)

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self, camera=FRONT, *extra, env=None):
        result = subprocess.run(
            [sys.executable, "-I", CORE, "snapshot", camera, "--size", "64x48",
             "--sysfs-root", self.sys, "--runtime-dir", self.run_dir, "--out", self.out,
             "--dev-root", self.dev, *extra],
            capture_output=True, text=True, timeout=60, env=env or self.env)
        return result.returncode, json.loads(result.stdout)

    def test_photo_saved(self):
        code, data = self.snap()
        self.assertEqual(code, 0, data)
        self.assertEqual(data["errors"], [])
        self.assertTrue(os.path.isfile(data["path"]))
        self.assertEqual(os.path.dirname(data["path"]), self.out)
        w, h, rows = read_png(data["path"])
        self.assertEqual((w, h), (64, 48))
        r, g, b = rows[0][0:3]                     # red patch top left (dark end of the ramp)
        self.assertTrue(r > 100 and g == 0, (r, g, b))
        self.assertGreater(rows[-1][-3], 240)      # bright end of the ramp
        self.assertEqual(data["exposure_us"], 33018)  # metadata of the last frame
        self.assertEqual(data["frames"], 30)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "snapshot")))
        self.assertIsNone(preview.read_lock(self.run_dir))

    def test_lens_camera_locked(self):
        code, data = self.snap(BACK)
        self.assertEqual(code, 1)
        self.assertEqual(data["errors"][0]["code"], "lens-blocked")
        self.assertFalse(os.path.exists(self.out))

    def test_usb_camera_blocked_without_hardware(self):
        # USB photos use plain V4L2 (D17); the tests never reach a real device.
        code, data = self.snap("usb:1-5")
        self.assertEqual(data["errors"][0]["code"], "hardware-blocked")

    def test_cam_failure_reported_and_cleaned(self):
        code, data = self.snap(env=dict(self.env, FAKE_CAM_EXIT="3"))
        self.assertEqual(code, 1)
        self.assertEqual(data["errors"][0]["code"], "cam-failed")
        self.assertIn("simulated failure", data["errors"][0]["detail"])
        self.assertIsNone(preview.read_lock(self.run_dir))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "snapshot")))

    def test_refused_while_preview_runs(self):
        holder = subprocess.Popen(["sleep", "30"])
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            preview.write_lock(self.run_dir, {"pid": holder.pid,
                                              "start_time": preview.start_time(holder.pid),
                                              "camera": FRONT, "viewer": "qcam"})
            code, data = self.snap()
            self.assertEqual(data["errors"][0]["code"], "preview-running")
            self.assertIsNotNone(preview.read_lock(self.run_dir))  # not touched
        finally:
            holder.kill()
            holder.wait()

    def test_hardware_blocked_without_fake_cam(self):
        env = {k: v for k, v in self.env.items() if k != "AVINCECAM_FAKE_CAM"}
        code, data = self.snap(env=env)
        self.assertEqual(data["errors"][0]["code"], "hardware-blocked")


if __name__ == "__main__":
    unittest.main()
