"""probe with simulated sysfs and recorded cam output – no hardware."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import fakesysfs
from test_cli import run
from test_libcamera import FIXTURES


class ProbeBase(unittest.TestCase):
    scenario = staticmethod(fakesysfs.latitude_5285)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "sys")
        self.ipa = os.path.join(self.tmp.name, "ipa")
        os.makedirs(self.ipa)
        os.makedirs(self.root)
        self.scenario(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, *extra, recorded=FIXTURES):
        argv = ["probe", *extra, "--sysfs-root", self.root, "--ipa-dir", self.ipa]
        if recorded is not None:
            argv += ["--tool-output", recorded]
        return run(argv)

    @staticmethod
    def by_id(data):
        return {c["id"]: c for c in data["cameras"]}


class ProbeLatitudeTest(ProbeBase):
    def test_both_cameras_get_details(self):
        code, data = self.probe()
        self.assertEqual(code, 0)
        self.assertEqual(data["errors"], [])
        cams = self.by_id(data)
        front = cams["sensor:i2c-INT3479:00"]
        back = cams["sensor:i2c-INT3477:00"]
        self.assertEqual(front["backend_id"], r"\_SB_.PCI0.LNK0")
        self.assertEqual(back["backend_id"], r"\_SB_.PCI0.LNK1")
        self.assertEqual(front["frame_rate_limits"]["max"], 30.0)
        self.assertEqual(back["sensor_size"], [3264, 2448])
        self.assertEqual(len(front["formats"]), 17)
        self.assertEqual(front["formats"][-1],
                         {"pixel_format": "NV12", "width": 1280, "height": 720, "fps": []})
        self.assertEqual(front["formats_source"], "libcamera cam (default stream)")
        self.assertNotIn("location-mismatch", front["warnings"])

    def test_recorded_run_says_so(self):
        _, data = self.probe()
        self.assertFalse(data["opens_devices"])
        self.assertIn("recorded", data["source"])

    def test_stops_after_both_cameras(self):
        _, data = self.probe()
        self.assertEqual(data["libcamera_calls"], 2)

    def test_front_only_needs_one_call(self):
        _, data = self.probe("sensor:i2c-INT3479:00")
        self.assertEqual(data["libcamera_calls"], 1)
        self.assertEqual([c["id"] for c in data["cameras"]], ["sensor:i2c-INT3479:00"])

    def test_back_only_needs_two_calls(self):
        _, data = self.probe("sensor:i2c-INT3477:00")
        self.assertEqual(data["libcamera_calls"], 2)
        self.assertEqual(data["cameras"][0]["backend_id"], r"\_SB_.PCI0.LNK1")

    def test_unknown_camera(self):
        code, data = self.probe("sensor:nonsense")
        self.assertEqual(code, 1)
        self.assertEqual(data["errors"][0]["code"], "unknown-camera")

    def test_no_recorded_output_means_no_match(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        code, data = self.probe(recorded=empty)
        self.assertEqual(code, 0)
        self.assertEqual(data["libcamera_calls"], 1)
        for cam in data["cameras"]:
            self.assertIsNone(cam["formats"])
            self.assertIn("libcamera-no-match", cam["warnings"])

    def test_unparsable_output_is_flagged(self):
        broken = os.path.join(self.tmp.name, "broken")
        shutil.copytree(FIXTURES, broken)
        with open(os.path.join(broken, "info-1.txt"), "w", encoding="utf-8") as f:
            f.write("Segmentation fault\n")
        _, data = self.probe(recorded=broken)
        self.assertIn("cam-output-unparsed", data["notes"])
        cams = self.by_id(data)
        self.assertIsNone(cams["sensor:i2c-INT3479:00"]["formats"])
        self.assertEqual(cams["sensor:i2c-INT3477:00"]["backend_id"], r"\_SB_.PCI0.LNK1")

    def test_hardware_blocked_without_recording(self):
        with mock.patch.dict(os.environ, {"AVINCECAM_NO_HARDWARE": "1"}):
            code, data = self.probe(recorded=None)
        self.assertEqual(code, 1)
        self.assertEqual(data["errors"][0]["code"], "hardware-blocked")
        self.assertTrue(data["opens_devices"])


def swapped_panels(root):
    fakesysfs.latitude_5285(root)
    for sensor, panel in (("i2c-INT3479:00", "back"), ("i2c-INT3477:00", "front")):
        path = os.path.join(root, "bus", "i2c", "devices", sensor,
                            "physical_location", "panel")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(panel + "\n")


class LocationMismatchTest(ProbeBase):
    scenario = staticmethod(swapped_panels)

    def test_mismatch_is_reported_not_hidden(self):
        _, data = self.probe()
        cams = self.by_id(data)
        front_sensor = cams["sensor:i2c-INT3479:00"]
        self.assertEqual(front_sensor["backend_id"], r"\_SB_.PCI0.LNK0")
        self.assertEqual(front_sensor["location"], "back")  # sysfs value kept
        self.assertIn("location-mismatch", front_sensor["warnings"])


class ProbeUvcTest(ProbeBase):
    scenario = staticmethod(fakesysfs.single_uvc)

    def test_uvc_needs_no_libcamera_call(self):
        code, data = self.probe()
        self.assertEqual(code, 0)
        self.assertEqual(data["libcamera_calls"], 0)
        self.assertEqual(data["cameras"][0]["formats_source"],
                         "v4l2 probing not implemented yet")


class ProbeMixedTest(ProbeBase):
    scenario = staticmethod(fakesysfs.ipu3_plus_uvc)

    def test_sensors_matched_uvc_untouched(self):
        _, data = self.probe()
        apis = {c["id"]: (c["api"], c["backend_id"]) for c in data["cameras"]}
        self.assertEqual(apis["sensor:i2c-INT3479:00"], ("libcamera", r"\_SB_.PCI0.LNK0"))
        self.assertEqual(apis["usb:1-5"], ("v4l2", None))


if __name__ == "__main__":
    unittest.main()
