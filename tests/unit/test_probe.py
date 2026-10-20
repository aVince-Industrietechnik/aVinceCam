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

    def test_back_only_needs_one_call(self):
        # Asked by its libcamera id, not by walking the cam indices.
        _, data = self.probe("sensor:i2c-INT3477:00")
        self.assertEqual(data["libcamera_calls"], 1)
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
        # The first id is unknown, then index 1 is unknown: stop.
        self.assertEqual(data["libcamera_calls"], 2)
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


# cam output of a USB webcam that libcamera lists before the built-in
# cameras (shape as on the Latitude, values made up for the test).
UVC_INFO = """Using camera \\_SB_.PCI0.XHC_.RHUB.HS09-9:1.0-0bda:5697 as cam0
0: 1280x720-MJPEG
 * Pixelformat: MJPEG (640x480)-(1280x720)/(+0,+0)
  - 640x480
  - 1280x720
Property: Location = 2
Property: Model = USB 2.0 Camera
"""


def webcam_first(directory):
    """Recorded output with the webcam at cam index 1 (see T6-1)."""
    with open(os.path.join(directory, "info-1.txt"), "w", encoding="utf-8") as f:
        f.write(UVC_INFO)
    for index in (1, 2):
        shutil.copy(os.path.join(FIXTURES, f"info-{index}.txt"),
                    os.path.join(directory, f"info-{index + 1}.txt"))


def without_acpi_paths(root):
    """Latitude with webcam, but without firmware_node/path: libcamera ids
    are not known from sysfs."""
    fakesysfs.ipu3_plus_uvc(root)
    for dirpath, _dirs, files in os.walk(root):
        if os.path.basename(dirpath) == "firmware_node" and "path" in files:
            os.remove(os.path.join(dirpath, "path"))


class WebcamFirstTest(ProbeBase):
    def setUp(self):
        super().setUp()
        self.recorded = os.path.join(self.tmp.name, "webcam-first")
        os.makedirs(self.recorded)
        webcam_first(self.recorded)

    def test_back_camera_needs_one_call(self):
        _, data = self.probe("sensor:i2c-INT3477:00", recorded=self.recorded)
        self.assertEqual(data["libcamera_calls"], 1)
        cam = data["cameras"][0]
        self.assertEqual(cam["backend_id"], r"\_SB_.PCI0.LNK1")
        self.assertEqual(cam["sensor_size"], [3264, 2448])

    def test_both_cameras_need_two_calls(self):
        _, data = self.probe(recorded=self.recorded)
        self.assertEqual(data["libcamera_calls"], 2)
        self.assertEqual(data["errors"], [])
        cams = self.by_id(data)
        self.assertEqual(cams["sensor:i2c-INT3479:00"]["backend_id"], r"\_SB_.PCI0.LNK0")
        self.assertEqual(cams["sensor:i2c-INT3477:00"]["backend_id"], r"\_SB_.PCI0.LNK1")


class WithoutAcpiPathTest(ProbeBase):
    scenario = staticmethod(without_acpi_paths)

    def test_falls_back_to_indices(self):
        recorded = os.path.join(self.tmp.name, "webcam-first")
        os.makedirs(recorded)
        webcam_first(recorded)
        _, data = self.probe("sensor:i2c-INT3477:00", recorded=recorded)
        # Index 1 is the webcam, 2 the front, 3 the back camera.
        self.assertEqual(data["libcamera_calls"], 3)
        self.assertEqual(data["cameras"][0]["backend_id"], r"\_SB_.PCI0.LNK1")
        self.assertNotIn("libcamera-no-match", data["cameras"][0]["warnings"])


def renamed_ids(directory):
    """libcamera names the cameras differently than the ACPI path."""
    for index in (1, 2):
        with open(os.path.join(FIXTURES, f"info-{index}.txt"), encoding="utf-8") as f:
            text = f.read().replace("\\_SB_.PCI0.LNK", "/base/sensor@")
        with open(os.path.join(directory, f"info-{index}.txt"), "w", encoding="utf-8") as f:
            f.write(text)


class UnknownIdTest(ProbeBase):
    def test_unknown_id_falls_back_to_indices(self):
        recorded = os.path.join(self.tmp.name, "renamed")
        os.makedirs(recorded)
        renamed_ids(recorded)
        _, data = self.probe(recorded=recorded)
        # One failed call by id, then indices 1 and 2.
        self.assertEqual(data["libcamera_calls"], 3)
        cams = self.by_id(data)
        self.assertEqual(cams["sensor:i2c-INT3479:00"]["backend_id"], "/base/sensor@0")
        self.assertEqual(cams["sensor:i2c-INT3477:00"]["backend_id"], "/base/sensor@1")


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
        with mock.patch.dict(os.environ, {"AVINCECAM_NO_HARDWARE": "1"}):
            code, data = self.probe()
        self.assertEqual(data["libcamera_calls"], 0)
        # The real V4L2 query is blocked in tests and reported, not faked.
        self.assertEqual(data["errors"][0]["code"], "hardware-blocked")
        self.assertIsNone(data["cameras"][0]["formats"])


class ProbeMixedTest(ProbeBase):
    scenario = staticmethod(fakesysfs.ipu3_plus_uvc)

    def test_sensors_matched_uvc_untouched(self):
        _, data = self.probe()
        apis = {c["id"]: (c["api"], c["backend_id"]) for c in data["cameras"]}
        self.assertEqual(apis["sensor:i2c-INT3479:00"], ("libcamera", r"\_SB_.PCI0.LNK0"))
        self.assertEqual(apis["usb:1-5"], ("v4l2", None))


if __name__ == "__main__":
    unittest.main()
