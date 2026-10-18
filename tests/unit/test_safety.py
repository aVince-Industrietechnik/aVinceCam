import tempfile
import unittest

from avincecam import safety
from fakesysfs import add_v4l_node, latitude_5285, single_uvc


class SafetyIpu3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        latitude_5285(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lens_motor_is_recognised(self):
        self.assertTrue(safety.is_lens_node("/dev/v4l-subdev8", self.root))

    def test_sensors_are_not_lens_nodes(self):
        self.assertFalse(safety.is_lens_node("/dev/v4l-subdev6", self.root))
        self.assertFalse(safety.is_lens_node("/dev/v4l-subdev7", self.root))

    def test_lens_motor_is_never_opened(self):
        with self.assertRaises(safety.SafetyError):
            safety.check_v4l2_query("/dev/v4l-subdev8", self.root)

    def test_sensor_subdev_is_never_opened(self):
        with self.assertRaises(safety.SafetyError):
            safety.check_v4l2_query("/dev/v4l-subdev6", self.root)

    def test_raw_ipu3_nodes_are_left_to_libcamera(self):
        for node in ("/dev/video10", "/dev/video3"):
            with self.assertRaises(safety.SafetyError):
                safety.check_v4l2_query(node, self.root)

    def test_unknown_node_is_refused(self):
        with self.assertRaises(safety.SafetyError):
            safety.check_v4l2_query("/dev/video99", self.root)


class SafetyUvcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        single_uvc(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_uvc_node_may_be_queried(self):
        safety.check_v4l2_query("/dev/video0", self.root)

    def test_driver_is_read_from_sysfs(self):
        self.assertEqual(safety.node_driver("/dev/video0", self.root), "uvcvideo")

    def test_vcm_name_without_known_driver(self):
        add_v4l_node(self.root, "v4l-subdev0", "foo-vcm", "unknownlens")
        self.assertTrue(safety.is_lens_node("/dev/v4l-subdev0", self.root))


if __name__ == "__main__":
    unittest.main()
