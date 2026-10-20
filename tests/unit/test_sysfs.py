# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
import builtins
import os
import tempfile
import unittest
from unittest import mock

import fakesysfs
from avincecam import sysfs
from test_cli import run


class Base(unittest.TestCase):
    scenario = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "sys")
        self.ipa = os.path.join(self.tmp.name, "ipa")
        os.makedirs(self.root)
        os.makedirs(self.ipa)
        if self.scenario:
            self.scenario(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self):
        return sysfs.scan(self.root, (self.ipa,))

    def by_id(self, cameras):
        return {c.id: c for c in cameras}


class Latitude5285Test(Base):
    scenario = staticmethod(fakesysfs.latitude_5285)

    def test_two_sensors_no_raw_nodes(self):
        cameras, unclassified = self.scan()
        self.assertEqual(sorted(c.id for c in cameras),
                         ["sensor:i2c-INT3477:00", "sensor:i2c-INT3479:00"])
        self.assertEqual(unclassified, [])

    def test_front_and_back_from_physical_location(self):
        cams = self.by_id(self.scan()[0])
        front = cams["sensor:i2c-INT3479:00"]
        back = cams["sensor:i2c-INT3477:00"]
        self.assertEqual((front.model, front.location, front.label),
                         ("ov5670", "front", "Front camera"))
        self.assertEqual((back.model, back.location, back.label),
                         ("ov8858", "back", "Back camera"))

    def test_pipeline_api_and_media_node(self):
        for cam in self.scan()[0]:
            self.assertEqual(cam.api, "libcamera")
            self.assertEqual(cam.pipeline, "ipu3")
            self.assertIn("/dev/media1", cam.nodes)
            self.assertNotIn("/dev/media0", cam.nodes)

    def test_lens_motor_only_at_back_camera(self):
        cams = self.by_id(self.scan()[0])
        self.assertIn("lens-not-controlled", cams["sensor:i2c-INT3477:00"].warnings)
        self.assertNotIn("lens-not-controlled", cams["sensor:i2c-INT3479:00"].warnings)
        self.assertTrue(cams["sensor:i2c-INT3477:00"].has_lens)
        self.assertFalse(cams["sensor:i2c-INT3479:00"].has_lens)

    def test_libcamera_id_from_acpi_path(self):
        cams = self.by_id(self.scan()[0])
        self.assertEqual(cams["sensor:i2c-INT3479:00"].backend_id, r"\_SB_.PCI0.LNK0")
        self.assertEqual(cams["sensor:i2c-INT3477:00"].backend_id, r"\_SB_.PCI0.LNK1")

    def test_lens_motor_is_no_camera_and_not_listed(self):
        cameras, _ = self.scan()
        for cam in cameras:
            self.assertNotIn("/dev/v4l-subdev8", cam.nodes)

    def test_missing_tuning_is_reported(self):
        for cam in self.scan()[0]:
            self.assertIn("no-sensor-tuning", cam.warnings)

    def test_existing_tuning_is_recognised(self):
        fakesysfs.add_ipa_tuning(self.ipa, "ipu3", "ov5670.yaml")
        cams = self.by_id(self.scan()[0])
        self.assertNotIn("no-sensor-tuning", cams["sensor:i2c-INT3479:00"].warnings)
        self.assertIn("no-sensor-tuning", cams["sensor:i2c-INT3477:00"].warnings)

    def test_status_is_not_guessed(self):
        for cam in self.scan()[0]:
            self.assertEqual(cam.status, "unknown")
            self.assertEqual(cam.status_certainty, "not checked")
            self.assertIsNone(cam.formats)


class SingleUvcTest(Base):
    scenario = staticmethod(fakesysfs.single_uvc)

    def test_one_camera_for_two_nodes(self):
        cameras, unclassified = self.scan()
        self.assertEqual(len(cameras), 1)
        cam = cameras[0]
        self.assertEqual(cam.id, "usb:1-5")
        self.assertEqual(cam.label, "HD Webcam")
        self.assertEqual(cam.api, "v4l2")
        self.assertEqual(cam.kind, "external")
        self.assertEqual(cam.nodes, ["/dev/video0", "/dev/video1", "/dev/media2"])
        self.assertEqual(unclassified, [])


class IntegratedUvcTest(Base):
    scenario = staticmethod(lambda root: fakesysfs.usb_webcam(root, removable="fixed"))

    def test_fixed_usb_camera_is_integrated(self):
        cam = self.scan()[0][0]
        self.assertEqual(cam.kind, "integrated")
        self.assertIsNone(cam.location)


class UnknownRemovableTest(Base):
    scenario = staticmethod(lambda root: fakesysfs.usb_webcam(root, removable="unknown"))

    def test_unknown_stays_unknown(self):
        self.assertEqual(self.scan()[0][0].kind, "unknown")


class MixedTest(Base):
    scenario = staticmethod(fakesysfs.ipu3_plus_uvc)

    def test_sensors_and_usb_camera(self):
        cameras, _ = self.scan()
        self.assertEqual([c.api for c in cameras], ["libcamera", "libcamera", "v4l2"])


class EmptyTest(Base):
    scenario = staticmethod(fakesysfs.empty)

    def test_no_cameras(self):
        self.assertEqual(self.scan(), ([], []))

    def test_missing_sysfs_is_no_crash(self):
        self.assertEqual(sysfs.scan(os.path.join(self.root, "missing"), ()), ([], []))


class UnknownDriverTest(Base):
    def test_unknown_video_node_is_reported_not_hidden(self):
        fakesysfs.add_v4l_node(self.root, "video40", "Dummy video device", "v4l2loopback",
                               bus="platform")
        cameras, unclassified = self.scan()
        self.assertEqual(cameras, [])
        self.assertEqual(unclassified, [{"dev": "/dev/video40",
                                         "name": "Dummy video device",
                                         "driver": "v4l2loopback"}])


class NoDeviceAccessTest(Base):
    """A scan must never open anything below /dev."""
    scenario = staticmethod(fakesysfs.ipu3_plus_uvc)

    def test_scan_opens_no_device_file(self):
        real_open = builtins.open
        opened = []

        def spy(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", spy), mock.patch("os.open") as raw_open:
            self.scan()
        self.assertFalse(raw_open.called)
        self.assertTrue(opened)
        self.assertFalse([p for p in opened if p.startswith("/dev")])
        self.assertFalse([p for p in opened if not p.startswith(self.tmp.name)])


class ScanCliTest(Base):
    scenario = staticmethod(fakesysfs.latitude_5285)

    def test_scan_json(self):
        code, data = run(["scan", "--sysfs-root", self.root, "--ipa-dir", self.ipa])
        self.assertEqual(code, 0)
        self.assertEqual(data["source"], "sysfs")
        self.assertFalse(data["opens_devices"])
        self.assertEqual(len(data["cameras"]), 2)
        self.assertEqual(data["errors"], [])


if __name__ == "__main__":
    unittest.main()
