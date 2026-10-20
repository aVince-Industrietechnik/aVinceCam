# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Photos of USB cameras (D17) with a simulated webcam – no hardware."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import fakesysfs
import fakeuvc
from avincecam import preview, safety, snapshot, uvcphoto
from avincecam.__main__ import main as core_main

try:
    from PIL import Image
except ImportError:  # optional: only used to decode the repaired JPEG
    Image = None

USB = "usb:1-5"


class ChooseModeTest(unittest.TestCase):
    def setUp(self):
        self.formats = fakeuvc.recorded_formats()

    def test_requested_size_with_mjpeg(self):
        self.assertEqual(uvcphoto.choose_mode(self.formats, (1600, 1200)), ("MJPG", 1600, 1200))

    def test_largest_smaller_size(self):
        pix, w, h = uvcphoto.choose_mode(self.formats, (1500, 1000))
        self.assertEqual(pix, "MJPG")
        self.assertTrue(w <= 1500 and h <= 1000, (w, h))
        self.assertEqual((w, h), (1280, 960))

    def test_smallest_larger_size_if_nothing_fits(self):
        formats = [{"pixel_format": "MJPG", "width": 1920, "height": 1080},
                   {"pixel_format": "MJPG", "width": 1280, "height": 720}]
        self.assertEqual(uvcphoto.choose_mode(formats, (320, 240)), ("MJPG", 1280, 720))

    def test_yuyv_only_and_nothing_usable(self):
        yuyv = [{"pixel_format": "YUYV", "width": 640, "height": 480}]
        self.assertEqual(uvcphoto.choose_mode(yuyv, (1600, 1200)), ("YUYV", 640, 480))
        self.assertIsNone(uvcphoto.choose_mode(
            [{"pixel_format": "H264", "width": 640, "height": 480}], (640, 480)))


class JpegTest(unittest.TestCase):
    def test_huffman_tables_added_before_the_scan(self):
        data = uvcphoto.complete_jpeg(fakeuvc.fake_jpeg())
        self.assertEqual(data.count(b"\xff\xc4"), 1)
        self.assertLess(data.index(b"\xff\xc4"), data.index(b"\xff\xda"))
        self.assertTrue(data.endswith(b"\xff\xd9"))

    def test_existing_tables_kept_and_trailer_cut(self):
        original = fakeuvc.fake_jpeg(with_dht=True)
        self.assertEqual(uvcphoto.complete_jpeg(original + b"\x00" * 100), original)

    def test_not_a_jpeg(self):
        for data in (b"", b"RIFF....", b"\xff\xd8\x00\x00garbage", b"\xff\xd8\xff\xdb\x00"):
            with self.assertRaises(uvcphoto.PhotoError):
                uvcphoto.complete_jpeg(data)

    @unittest.skipIf(Image is None, "Pillow not installed")
    def test_repaired_jpeg_decodes_like_the_original(self):
        source = Image.new("RGB", (48, 32))
        for x in range(48):
            for y in range(32):
                source.putpixel((x, y), (x * 5, y * 7, 128))
        buf = io.BytesIO()
        source.save(buf, "JPEG", quality=90, optimize=False)
        original = buf.getvalue()
        # strip every DHT segment, as many webcams do
        pos, stripped = 2, bytearray(original[:2])
        while original[pos + 1] != 0xDA:
            length = int.from_bytes(original[pos + 2:pos + 4], "big")
            if original[pos + 1] != 0xC4:
                stripped += original[pos:pos + 2 + length]
            pos += 2 + length
        stripped += original[pos:]
        self.assertNotIn(b"\xff\xc4", bytes(stripped[:pos]))
        repaired = uvcphoto.complete_jpeg(bytes(stripped))
        a = Image.open(io.BytesIO(original)).convert("RGB").tobytes()
        b = Image.open(io.BytesIO(repaired)).convert("RGB").tobytes()
        self.assertEqual(a, b)


class YuyvTest(unittest.TestCase):
    def test_white_and_size_check(self):
        rows = list(uvcphoto.yuyv_rows(fakeuvc.yuyv_frame(4, 2), 4, 2))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], b"\xff" * 12)
        with self.assertRaises(uvcphoto.PhotoError):
            list(uvcphoto.yuyv_rows(b"\x00" * 10, 4, 2))


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = os.path.join(self.tmp.name, "sys")
        os.makedirs(self.sys)
        fakesysfs.single_uvc(self.sys)

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_node_and_last_frame(self):
        fake = fakeuvc.FakeUvc()
        node, formats = uvcphoto.capture_node(["/dev/video0", "/dev/video1", "/dev/media0"],
                                              self.sys, fake)
        self.assertEqual(node, "/dev/video0")
        frame = uvcphoto.capture(node, self.sys, ("MJPG", 1600, 1200), fake)
        self.assertEqual(frame["pixel_format"], "MJPG")
        self.assertGreaterEqual(frame["frames"], uvcphoto.MIN_FRAMES)
        self.assertTrue(frame["data"].startswith(b"\xff\xd8"))
        self.assertEqual(fake.open_fds, set())             # every node closed again
        self.assertTrue(all(m.closed for m in fake.maps))  # every buffer unmapped
        self.assertIn(uvcphoto.VIDIOC_STREAMOFF, fake.calls)
        self.assertTrue(all(p.startswith("/dev/video") for p in fake.opened))

    def test_empty_frames_are_skipped(self):
        fake = fakeuvc.FakeUvc(empty_frames=5)
        frame = uvcphoto.capture("/dev/video0", self.sys, ("MJPG", 640, 480), fake)
        self.assertGreaterEqual(frame["frames"], uvcphoto.MIN_FRAMES)

    def test_refused_format(self):
        fake = fakeuvc.FakeUvc(refuse_format=True)
        with self.assertRaises(uvcphoto.PhotoError) as ctx:
            uvcphoto.capture("/dev/video0", self.sys, ("MJPG", 640, 480), fake)
        self.assertEqual(ctx.exception.code, "format-refused")
        self.assertEqual(fake.open_fds, set())

    def test_no_frame_within_the_time_limit(self):
        fake = fakeuvc.FakeUvc(no_frames=True)
        with self.assertRaises(uvcphoto.PhotoError) as ctx:
            uvcphoto.capture("/dev/video0", self.sys, ("MJPG", 640, 480), fake, max_seconds=1.0)
        self.assertEqual(ctx.exception.code, "no-frame")
        self.assertIn(uvcphoto.VIDIOC_STREAMOFF, fake.calls)
        self.assertEqual(fake.open_fds, set())

    def test_only_uvc_video_nodes_are_opened(self):
        fake = fakeuvc.FakeUvc()
        with self.assertRaises(safety.SafetyError):
            uvcphoto.capture("/dev/v4l-subdev0", self.sys, ("MJPG", 640, 480), fake)
        self.assertEqual(fake.opened, [])

    def test_real_backend_blocked_in_tests(self):
        with mock.patch.dict(os.environ, {"AVINCECAM_NO_HARDWARE": "1"}):
            with self.assertRaises(uvcphoto.v4l2.HardwareBlocked):
                uvcphoto.Backend()


class SnapshotCommandTest(unittest.TestCase):
    """The core's snapshot command for a USB camera, with the fake backend."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = os.path.join(self.tmp.name, "sys")
        os.makedirs(self.sys)
        fakesysfs.ipu3_plus_uvc(self.sys)
        self.dev = os.path.join(self.tmp.name, "dev")
        os.makedirs(os.path.join(self.dev, "dev"))
        self.run_dir = os.path.join(self.tmp.name, "run")
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        uvcphoto.TEST_BACKEND = None
        self.tmp.cleanup()

    @staticmethod
    def webcam(**kw):
        return fakeuvc.FakeUvc(capture_node="video20", **kw)

    def snap(self, fake, *extra):
        uvcphoto.TEST_BACKEND = fake
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = core_main(["snapshot", USB, "--size", "1600x1200", "--sysfs-root", self.sys,
                              "--dev-root", self.dev, "--runtime-dir", self.run_dir,
                              "--out", self.out, *extra])
        return code, json.loads(out.getvalue())

    def test_jpeg_saved(self):
        code, data = self.snap(self.webcam())
        self.assertEqual(code, 0, data)
        self.assertEqual(data["format"], "MJPG")
        self.assertEqual(data["size"], [1600, 1200])
        self.assertTrue(data["path"].endswith(".jpg"))
        with open(data["path"], "rb") as handle:
            saved = handle.read()
        self.assertIn(b"\xff\xc4", saved)
        self.assertIsNone(preview.read_lock(self.run_dir))

    def test_yuyv_camera_saves_png(self):
        fake = self.webcam(formats=[{"pixel_format": "YUYV", "width": 32, "height": 16}])
        code, data = self.snap(fake)
        self.assertEqual(code, 0, data)
        self.assertTrue(data["path"].endswith(".png"))
        with open(data["path"], "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")

    def test_no_usable_format(self):
        fake = self.webcam(formats=[{"pixel_format": "H264", "width": 640, "height": 480}])
        code, data = self.snap(fake)
        self.assertEqual(data["errors"][0]["code"], "no-photo-format")
        self.assertFalse(os.path.exists(self.out))

    def test_damaged_frame(self):
        code, data = self.snap(self.webcam(frame=b"not a jpeg"))
        self.assertEqual(data["errors"][0]["code"], "bad-frame")
        self.assertIsNone(preview.read_lock(self.run_dir))

    def test_refused_while_preview_runs(self):
        os.makedirs(self.run_dir)
        preview.write_lock(self.run_dir, {"pid": os.getppid(),
                                          "start_time": preview.start_time(os.getppid()),
                                          "camera": USB, "viewer": "vlc"})
        fake = self.webcam()
        code, data = self.snap(fake)
        self.assertEqual(data["errors"][0]["code"], "preview-running")
        self.assertEqual(fake.opened, [])

    def test_no_libcamera_node_is_touched(self):
        fake = self.webcam()
        code, data = self.snap(fake)
        self.assertEqual(code, 0, data)
        self.assertTrue(set(fake.opened) <= {"/dev/video20", "/dev/video21"}, fake.opened)


if __name__ == "__main__":
    unittest.main()
