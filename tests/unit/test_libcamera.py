import os
import unittest
from unittest import mock

from avincecam import libcamera

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "cam-0.2.0")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


class ParseListTest(unittest.TestCase):
    def test_list(self):
        self.assertEqual(libcamera.parse_list(fixture("list.txt")), [
            {"index": 1, "name": "Internal front camera", "id": r"\_SB_.PCI0.LNK0"},
            {"index": 2, "name": "Internal back camera", "id": r"\_SB_.PCI0.LNK1"},
        ])


class ParseInfoFrontTest(unittest.TestCase):
    def setUp(self):
        self.info = libcamera.parse_info(fixture("info-1.txt"))

    def test_id_model_location(self):
        self.assertEqual(self.info["id"], r"\_SB_.PCI0.LNK0")
        self.assertEqual(libcamera.model(self.info), "ov5670")
        self.assertEqual(libcamera.location(self.info), "front")

    def test_sensor_size(self):
        self.assertEqual(libcamera.sensor_size(self.info), [2624, 1980])

    def test_frame_rates(self):
        self.assertEqual(libcamera.frame_rate_limits(self.info),
                         {"min": 0.94, "max": 30.0,
                          "source": "libcamera FrameDurationLimits"})

    def test_formats(self):
        fmts = libcamera.formats(self.info)
        self.assertEqual(len(fmts), 17)
        self.assertEqual(fmts[0], ("NV12", 160, 120))
        self.assertEqual(fmts[-1], ("NV12", 1280, 720))

    def test_controls_with_complex_values(self):
        self.assertEqual(self.info["controls"]["ScalerCrop"],
                         ("(0, 0)/1348x739", "(0, 0)/2600x1952"))

    def test_no_focus_controls(self):
        for name in ("LensPosition", "AfMode", "AfTrigger"):
            self.assertNotIn(name, self.info["controls"])


class ParseInfoBackTest(unittest.TestCase):
    def test_back_camera(self):
        info = libcamera.parse_info(fixture("info-2.txt"))
        self.assertEqual(libcamera.model(info), "ov8858")
        self.assertEqual(libcamera.location(info), "back")
        self.assertEqual(libcamera.sensor_size(info), [3264, 2448])
        self.assertEqual(libcamera.frame_rate_limits(info)["max"], 30.03)
        self.assertEqual(libcamera.frame_rate_limits(info)["min"], 2.27)


class RobustnessTest(unittest.TestCase):
    def test_garbage_is_not_parsed(self):
        self.assertIsNone(libcamera.parse_info("Segmentation fault\n"))
        self.assertIsNone(libcamera.parse_info(""))

    def test_float_control_range(self):
        info = libcamera.parse_info(
            "Using camera X as cam0\nControl: Brightness: [-1.000000..1.000000]\n")
        self.assertEqual(info["controls"]["Brightness"], ("-1.000000", "1.000000"))

    def test_missing_frame_duration_gives_none(self):
        info = libcamera.parse_info("Using camera X as cam0\n")
        self.assertIsNone(libcamera.frame_rate_limits(info))

    def test_zero_frame_duration_gives_none(self):
        info = libcamera.parse_info(
            "Using camera X as cam0\nControl: FrameDurationLimits: [0..0]\n")
        self.assertIsNone(libcamera.frame_rate_limits(info))

    def test_unknown_location_stays_unknown(self):
        info = libcamera.parse_info("Using camera X as cam0\nProperty: Location = 7\n")
        self.assertIsNone(libcamera.location(info))


class RunnerTest(unittest.TestCase):
    def test_recorded_runner(self):
        runner = libcamera.RecordedRunner(FIXTURES)
        self.assertEqual(runner.run(["--list"]).returncode, 0)
        self.assertEqual(runner.run(libcamera.info_args(2)).returncode, 0)
        self.assertNotEqual(runner.run(libcamera.info_args(3)).returncode, 0)

    def test_probe_stops_at_unknown_index(self):
        runner = libcamera.RecordedRunner(FIXTURES)
        found, errors, calls = libcamera.probe_cameras(runner, 5)
        self.assertEqual([i for i, _, _ in found], [1, 2])
        self.assertEqual(errors, [])
        self.assertEqual(calls, 3)  # the third call finds no camera

    def test_probe_stops_early(self):
        runner = libcamera.RecordedRunner(FIXTURES)
        found, _, calls = libcamera.probe_cameras(runner, 5, stop=lambda f: True)
        self.assertEqual(len(found), 1)
        self.assertEqual(calls, 1)

    def test_recorded_runner_by_id(self):
        runner = libcamera.RecordedRunner(FIXTURES)
        result = runner.run(libcamera.info_args(r"\_SB_.PCI0.LNK1"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(libcamera.parse_info(result.stdout)["id"], r"\_SB_.PCI0.LNK1")
        self.assertNotEqual(runner.run(libcamera.info_args(r"\_SB_.PCI0.LNK9")).returncode, 0)

    def test_probe_by_ids_one_call_each(self):
        runner = libcamera.RecordedRunner(FIXTURES)
        found, errors, calls, complete = libcamera.probe_by_ids(
            runner, [r"\_SB_.PCI0.LNK1", r"\_SB_.PCI0.LNK0"])
        self.assertEqual([info["id"] for _, info, _ in found],
                         [r"\_SB_.PCI0.LNK1", r"\_SB_.PCI0.LNK0"])
        self.assertEqual((errors, calls, complete), ([], 2, True))

    def test_probe_by_ids_stops_at_unknown_id(self):
        runner = libcamera.RecordedRunner(FIXTURES)
        found, errors, calls, complete = libcamera.probe_by_ids(
            runner, ["nonsense", r"\_SB_.PCI0.LNK0"])
        self.assertEqual((found, errors, calls, complete), ([], [], 1, False))

    def test_probe_by_ids_timeout(self):
        class Slow:
            def run(self, args):
                return libcamera.Result(124, "", "timeout")
        found, errors, calls, complete = libcamera.probe_by_ids(Slow(), ["a", "b"])
        self.assertEqual(errors[0]["code"], "timeout")
        self.assertEqual((found, calls, complete), ([], 1, True))

    def test_live_runner_is_blocked_in_tests(self):
        with mock.patch.dict(os.environ, {"AVINCECAM_NO_HARDWARE": "1"}):
            with mock.patch("subprocess.run") as run:
                with self.assertRaises(libcamera.HardwareBlocked):
                    libcamera.LiveRunner().run(["--list"])
                self.assertFalse(run.called)

    def test_missing_tool(self):
        with mock.patch.dict(os.environ, {"AVINCECAM_NO_HARDWARE": "0"}):
            with self.assertRaises(libcamera.ToolMissing):
                libcamera.LiveRunner(tool="avincecam-no-such-tool").run(["--list"])

    def test_timeout_is_reported(self):
        class Slow:
            def run(self, args):
                return libcamera.Result(124, "", "timeout")
        found, errors, calls = libcamera.probe_cameras(Slow(), 2)
        self.assertEqual(found, [])
        self.assertEqual(errors[0]["code"], "timeout")
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
