# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""V4L2 queries against a simulated UVC camera – no device is opened."""

import errno
import os
import tempfile
import unittest
from unittest import mock

import fakesysfs
from avincecam import safety, v4l2


def code(text):
    return sum(ord(c) << (8 * i) for i, c in enumerate(text.ljust(4)))


class FakeCamera:
    """Answers V4L2 enumeration ioctls like a UVC webcam would."""

    def __init__(self, capture=True, formats=None, stepwise=False):
        self.capture = capture
        self.stepwise = stepwise
        self.formats = formats if formats is not None else [
            ("MJPG", "Motion-JPEG", [((1920, 1080), [30]), ((1280, 720), [30, 15])]),
            ("YUYV", "YUYV 4:2:2", [((640, 480), [30, 20, 15])]),
        ]
        self.calls = []

    def ioctl(self, fd, request, s):
        self.calls.append(request)
        if request == v4l2.VIDIOC_QUERYCAP:
            s.card = b"USB 2.0 Camera"
            s.capabilities = v4l2.V4L2_CAP_DEVICE_CAPS | v4l2.V4L2_CAP_VIDEO_CAPTURE
            s.device_caps = v4l2.V4L2_CAP_VIDEO_CAPTURE if self.capture else 0x00800000
            return
        if request == v4l2.VIDIOC_ENUM_FMT:
            if s.index >= len(self.formats):
                raise OSError(errno.EINVAL, "end")
            name, desc, _ = self.formats[s.index]
            s.pixelformat = code(name)
            s.description = desc.encode()
            return
        fmt = next(f for f in self.formats if code(f[0]) == s.pixel_format)
        if request == v4l2.VIDIOC_ENUM_FRAMESIZES:
            if self.stepwise:
                if s.index > 0:
                    raise OSError(errno.EINVAL, "end")
                s.type = 3  # continuous
                return
            if s.index >= len(fmt[2]):
                raise OSError(errno.EINVAL, "end")
            s.type = v4l2.V4L2_FRMSIZE_TYPE_DISCRETE
            s.u.discrete.width, s.u.discrete.height = fmt[2][s.index][0]
            return
        if request == v4l2.VIDIOC_ENUM_FRAMEINTERVALS:
            rates = next(r for (w, h), r in fmt[2] if (w, h) == (s.width, s.height))
            if s.index >= len(rates):
                raise OSError(errno.EINVAL, "end")
            s.type = v4l2.V4L2_FRMIVAL_TYPE_DISCRETE
            s.u.discrete.numerator, s.u.discrete.denominator = 1, rates[s.index]
            return
        raise OSError(errno.ENOTTY, "unexpected ioctl")


class V4l2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        fakesysfs.ipu3_plus_uvc(self.root)
        self.opened, self.closed = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, path):
        self.opened.append(path)
        return 99

    def query(self, cam, node="/dev/video20"):
        return v4l2.query_device(node, self.root, ioctl=cam.ioctl,
                                 opener=self.opener, closer=self.closed.append)

    def test_ioctl_numbers_match_the_kernel_headers(self):
        self.assertEqual(v4l2.VIDIOC_QUERYCAP, 0x80685600)
        self.assertEqual(v4l2.VIDIOC_ENUM_FMT, 0xC0405602)
        self.assertEqual(v4l2.VIDIOC_ENUM_FRAMESIZES, 0xC02C564A)
        self.assertEqual(v4l2.VIDIOC_ENUM_FRAMEINTERVALS, 0xC034564B)

    def test_formats_sizes_and_rates(self):
        result = self.query(FakeCamera())
        self.assertEqual(result["card"], "USB 2.0 Camera")
        self.assertEqual(result["formats"][0], {"pixel_format": "MJPG",
                                                "description": "Motion-JPEG",
                                                "width": 1920, "height": 1080, "fps": [30.0]})
        self.assertEqual(result["formats"][1]["fps"], [15.0, 30.0])
        self.assertEqual(result["formats"][2]["pixel_format"], "YUYV")
        self.assertFalse(result["stepwise"])
        formats, limits = v4l2.summarize(result)
        self.assertEqual((limits["min"], limits["max"]), (15.0, 30.0))
        self.assertEqual(self.closed, [99])

    def test_only_enumeration_ioctls_are_used(self):
        cam = FakeCamera()
        self.query(cam)
        allowed = {v4l2.VIDIOC_QUERYCAP, v4l2.VIDIOC_ENUM_FMT,
                   v4l2.VIDIOC_ENUM_FRAMESIZES, v4l2.VIDIOC_ENUM_FRAMEINTERVALS}
        self.assertTrue(set(cam.calls) <= allowed)

    def test_metadata_node_has_no_formats(self):
        self.assertIsNone(self.query(FakeCamera(capture=False), "/dev/video21"))
        self.assertEqual(self.closed, [99])

    def test_stepwise_sizes_are_flagged_not_invented(self):
        result = self.query(FakeCamera(stepwise=True))
        self.assertTrue(result["stepwise"])
        self.assertEqual(result["formats"], [])

    def test_ipu3_node_is_never_opened(self):
        with self.assertRaises(safety.SafetyError):
            self.query(FakeCamera(), "/dev/video10")
        with self.assertRaises(safety.SafetyError):
            self.query(FakeCamera(), "/dev/v4l-subdev8")
        self.assertEqual(self.opened, [])

    def test_hardware_blocked_without_injected_ioctl(self):
        with mock.patch.dict(os.environ, {"AVINCECAM_NO_HARDWARE": "1"}):
            with self.assertRaises(v4l2.HardwareBlocked):
                v4l2.query_device("/dev/video20", self.root, opener=self.opener)
        self.assertEqual(self.opened, [])

    def test_device_closed_after_error(self):
        def broken(fd, request, s):
            raise OSError(errno.EIO, "I/O error")
        with self.assertRaises(OSError):
            v4l2.query_device("/dev/video20", self.root, ioctl=broken,
                              opener=self.opener, closer=self.closed.append)
        self.assertEqual(self.closed, [99])


class ProbeUvcCliTest(unittest.TestCase):
    """probe fills UVC formats; the device query itself is simulated."""

    def test_probe_uvc_camera(self):
        from test_cli import run
        with tempfile.TemporaryDirectory() as tmp:
            fakesysfs.single_uvc(tmp)
            cam = FakeCamera()
            real = v4l2.query_device

            def fake_query(node, sysfs_root="/sys", **kw):
                return real(node, sysfs_root, ioctl=cam.ioctl,
                            opener=lambda p: 7, closer=lambda fd: None)

            with mock.patch.object(v4l2, "query_device", fake_query):
                code_, data = run(["probe", "--sysfs-root", tmp])
        self.assertEqual(code_, 0, data)
        uvc = data["cameras"][0]
        self.assertEqual(uvc["formats_source"], "V4L2 /dev/video0")
        self.assertEqual(len(uvc["formats"]), 3)
        self.assertEqual(uvc["frame_rate_limits"]["max"], 30.0)
        self.assertEqual(data["libcamera_calls"], 0)


if __name__ == "__main__":
    unittest.main()
