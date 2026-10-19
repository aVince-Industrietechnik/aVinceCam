"""Preview start/stop with a fake viewer – no camera is touched.

The core replaces itself with the viewer (exec), so these tests run the core
as a real subprocess.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import fakesysfs
from avincecam import preview
from avincecam.model import Camera

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORE = os.path.join(REPO, "core", "avincecam-core")
FAKE_VIEWER = os.path.join(REPO, "tests", "fake-viewer", "avincecam-fake-viewer")
FRONT = "sensor:i2c-INT3479:00"
BACK = "sensor:i2c-INT3477:00"


class PreviewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "sys")
        self.run_dir = os.path.join(self.tmp.name, "run")
        os.makedirs(self.root)
        fakesysfs.latitude_5285(self.root)
        self.procs = []
        self.env = dict(os.environ, AVINCECAM_NO_HARDWARE="1",
                        AVINCECAM_FAKE_VIEWER=FAKE_VIEWER, FAKE_VIEWER_SECONDS="30")

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.tmp.cleanup()

    def core(self, *args, env=None):
        result = subprocess.run([sys.executable, "-I", CORE, *args, "--runtime-dir", self.run_dir],
                                capture_output=True, text=True, timeout=30, env=env or self.env)
        return result.returncode, json.loads(result.stdout)

    def start(self, camera=FRONT, *extra, env=None):
        """Start a preview; returns (process, first JSON answer)."""
        proc = subprocess.Popen(
            [sys.executable, "-I", CORE, "preview", camera, "--sysfs-root", self.root,
             "--runtime-dir", self.run_dir, *extra],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env or self.env)
        self.procs.append(proc)
        return proc, json.loads(proc.stdout.readline())


class StartStopTest(PreviewBase):
    def test_start_status_stop(self):
        proc, answer = self.start()
        self.assertTrue(answer["started"], answer)
        self.assertEqual(answer["pid"], proc.pid)  # the viewer kept the core's PID

        code, status = self.core("status")
        self.assertTrue(status["preview"]["running"])
        self.assertEqual(status["preview"]["camera"], FRONT)

        code, stopped = self.core("stop")
        self.assertTrue(stopped["stopped"])
        self.assertFalse(stopped["forced"])
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertIsNone(preview.read_lock(self.run_dir))

    def test_viewer_gets_libcamera_id(self):
        proc, _ = self.start()
        time.sleep(0.5)
        self.core("stop")
        proc.wait(timeout=10)
        with open(os.path.join(self.run_dir, preview.LOG_NAME)) as log:
            self.assertIn(r"qcam -c \_SB_.PCI0.LNK0", log.read())

    def test_second_preview_is_refused(self):
        self.start(FRONT)
        proc2, answer = self.start(BACK, "--allow-lens")
        self.assertEqual(answer["errors"][0]["code"], "preview-running")
        self.assertEqual(proc2.wait(timeout=10), 1)

    def test_camera_with_focus_motor_is_locked_by_default(self):
        proc, answer = self.start(BACK)
        self.assertEqual(answer["errors"][0]["code"], "lens-blocked")
        self.assertEqual(proc.wait(timeout=10), 1)
        self.assertIsNone(preview.read_lock(self.run_dir))

    def test_camera_with_focus_motor_when_allowed(self):
        proc, answer = self.start(BACK, "--allow-lens")
        self.assertTrue(answer["started"], answer)
        self.core("stop")
        proc.wait(timeout=10)

    def test_front_camera_needs_no_permission(self):
        proc, answer = self.start(FRONT)
        self.assertTrue(answer["started"], answer)
        self.core("stop")
        proc.wait(timeout=10)

    def test_stop_without_preview(self):
        code, result = self.core("stop")
        self.assertEqual((code, result["stopped"], result["reason"]), (0, False, "no-preview"))

    def test_hanging_viewer_is_forced(self):
        env = dict(self.env, FAKE_VIEWER_IGNORE_TERM="1")
        proc, _ = self.start(env=env)
        time.sleep(0.5)  # let the viewer install its signal handler
        code, result = self.core("stop")
        self.assertTrue(result["stopped"])
        self.assertTrue(result["forced"])
        proc.wait(timeout=10)

    def test_failed_viewer_leaves_stale_lock_that_is_cleared(self):
        env = dict(self.env, FAKE_VIEWER_EXIT="3")
        proc, answer = self.start(env=env)
        self.assertEqual(proc.wait(timeout=10), 3)
        code, status = self.core("status")
        self.assertFalse(status["preview"]["running"])
        proc2, answer2 = self.start()  # a new preview is possible again
        self.assertTrue(answer2["started"])


class SafetyTest(PreviewBase):
    def test_foreign_process_is_never_stopped(self):
        foreign = subprocess.Popen(["sleep", "30"])
        self.procs.append(foreign)
        os.makedirs(self.run_dir, exist_ok=True)
        # A lock that names the foreign PID but not its start time
        # (e.g. the PID was reused after our viewer ended).
        preview.write_lock(self.run_dir, {"pid": foreign.pid, "start_time": 1,
                                          "camera": FRONT})
        code, result = self.core("stop")
        self.assertFalse(result["stopped"])
        time.sleep(0.3)
        self.assertIsNone(foreign.poll(), "foreign process must still run")

    def test_busy_camera_is_not_started(self):
        dev_root = os.path.join(self.tmp.name, "devroot")
        os.makedirs(os.path.join(dev_root, "dev"))
        node = os.path.join(dev_root, "dev", "media1")
        open(node, "w").close()
        holder = subprocess.Popen([sys.executable, "-c",
                                   f"import time; f=open({node!r}); time.sleep(30)"])
        self.procs.append(holder)
        time.sleep(0.5)
        proc, answer = self.start(FRONT, "--dev-root", dev_root)
        self.assertEqual(answer["errors"][0]["code"], "camera-busy")
        self.assertEqual(answer["holders"][0]["pid"], holder.pid)
        self.assertEqual(proc.wait(timeout=10), 1)
        self.assertIsNone(preview.read_lock(self.run_dir))

    def test_other_ipu3_camera_also_counts_as_busy(self):
        """On IPU3 a stream of the back camera blocks the front camera."""
        dev_root = os.path.join(self.tmp.name, "devroot")
        os.makedirs(os.path.join(dev_root, "dev"))
        node = os.path.join(dev_root, "dev", "video3")  # IMGU viewfinder node
        open(node, "w").close()
        holder = subprocess.Popen([sys.executable, "-c",
                                   f"import time; f=open({node!r}); time.sleep(30)"])
        self.procs.append(holder)
        time.sleep(0.5)
        _, answer = self.start(FRONT, "--dev-root", dev_root)
        self.assertEqual(answer["errors"][0]["code"], "camera-busy")

    def test_hardware_blocked_without_fake_viewer(self):
        env = {k: v for k, v in self.env.items() if k != "AVINCECAM_FAKE_VIEWER"}
        proc, answer = self.start(env=env)
        self.assertEqual(answer["errors"][0]["code"], "hardware-blocked")
        self.assertEqual(proc.wait(timeout=10), 1)

    def test_unknown_camera(self):
        _, answer = self.start("sensor:nonsense")
        self.assertEqual(answer["errors"][0]["code"], "unknown-camera")


class UnitTest(unittest.TestCase):
    def test_zombie_counts_as_ended(self):
        with tempfile.TemporaryDirectory() as proc_root:
            os.makedirs(os.path.join(proc_root, "4242"))
            fields = ["Z"] + ["0"] * 18 + ["777"]
            with open(os.path.join(proc_root, "4242", "stat"), "w") as f:
                f.write("4242 (qcam (x)) " + " ".join(fields) + "\n")
            lock = {"pid": 4242, "start_time": 777}
            self.assertFalse(preview.lock_alive(lock, proc_root))
            with open(os.path.join(proc_root, "4242", "stat"), "w") as f:
                f.write("4242 (qcam (x)) " + " ".join(["S"] + fields[1:]) + "\n")
            self.assertTrue(preview.lock_alive(lock, proc_root))

    def test_viewer_commands(self):
        cam = Camera(id=FRONT, label="Front camera", api="libcamera",
                     backend_id=r"\_SB_.PCI0.LNK0")
        self.assertEqual(preview.viewer_command(cam, "qcam", (1280, 720)),
                         ["qcam", "-c", r"\_SB_.PCI0.LNK0"])
        gst = preview.viewer_command(cam, "gst", (640, 480))
        self.assertIn(r"camera-name=\_SB_.PCI0.LNK0", gst)
        self.assertIn("video/x-raw,width=640,height=480", gst)

        uvc = Camera(id="usb:1-5", label="Webcam", api="v4l2",
                     nodes=["/dev/video0", "/dev/video1", "/dev/media2"])
        with mock.patch.object(preview, "which", lambda p: None):
            self.assertIn("device=/dev/video0", preview.viewer_command(uvc, "qcam", (1, 1)))
        with mock.patch.object(preview, "which", lambda p: "/usr/bin/" + p):
            self.assertIn("v4l2:///dev/video0", preview.viewer_command(uvc, "qcam", (1, 1)))

        no_id = Camera(id=FRONT, label="x", api="libcamera")
        with self.assertRaises(preview.PreviewError):
            preview.viewer_command(no_id, "qcam", (1280, 720))


class UvcModeTest(unittest.TestCase):
    """Preview mode for the recorded formats of a real USB 2.0 webcam."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO, "tests", "fixtures", "uvc", "realtek-0bda-5697.json")
        with open(path) as f:
            cls.formats = json.load(f)["formats"]
        cls.cam = Camera(id="usb:1-9", label="USB 2.0 Camera", api="v4l2",
                         nodes=["/dev/video14", "/dev/video15", "/dev/media2"])

    def test_mjpg_preferred_at_requested_size(self):
        self.assertEqual(preview.choose_uvc_mode(self.formats, (1280, 720)),
                         ("MJPG", 1280, 720, 30.0))

    def setUp(self):
        # Default for these tests: no VLC, so the GStreamer fallback is used.
        patcher = mock.patch.object(preview, "which", lambda p: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_vlc_preferred_when_installed(self):
        with mock.patch.object(preview, "which", lambda p: "/usr/bin/" + p):
            argv = preview.viewer_command(self.cam, "qcam", (1280, 720), self.formats)
        self.assertEqual(argv[0], "vlc")
        self.assertIn("--qt-minimal-view", argv)
        self.assertIn("--play-and-exit", argv)
        self.assertIn("v4l2:///dev/video14", argv)
        self.assertEqual(argv[-4:], [":v4l2-chroma=MJPG", ":v4l2-width=1280",
                                     ":v4l2-height=720", ":v4l2-fps=30"])

    def test_vlc_without_known_formats(self):
        with mock.patch.object(preview, "which", lambda p: "/usr/bin/" + p):
            argv = preview.viewer_command(self.cam, "qcam", (1280, 720), None)
        self.assertEqual(argv[-1], "v4l2:///dev/video14")

    def test_gstreamer_fallback_uses_jpeg_decoder(self):
        argv = preview.viewer_command(self.cam, "qcam", (1280, 720), self.formats)
        self.assertEqual(argv[0], "gst-launch-1.0")
        self.assertIn("image/jpeg,width=1280,height=720,framerate=30/1", argv)
        self.assertIn("jpegdec", argv)
        self.assertIn("device=/dev/video14", argv)

    def test_unknown_size_takes_largest_smaller_one(self):
        self.assertEqual(preview.choose_uvc_mode(self.formats, (1000, 700))[1:3], (800, 600))

    def test_yuyv_only_camera(self):
        yuyv = [f for f in self.formats if f["pixel_format"] == "YUYV"]
        mode = preview.choose_uvc_mode(yuyv, (640, 480))
        self.assertEqual(mode, ("YUYV", 640, 480, 30.0))
        argv = preview.viewer_command(self.cam, "qcam", (640, 480), yuyv)
        self.assertIn("video/x-raw,format=YUY2,width=640,height=480,framerate=30/1", argv)

    def test_unknown_formats_let_gstreamer_choose(self):
        argv = preview.viewer_command(self.cam, "qcam", (1280, 720), None)
        self.assertEqual(argv[-3:], ["videoconvert", "!", "autovideosink"])
        self.assertNotIn("jpegdec", argv)

    def test_fractional_rate(self):
        fmts = [{"pixel_format": "MJPG", "width": 640, "height": 480, "fps": [7.5]}]
        argv = preview.viewer_command(self.cam, "qcam", (640, 480), fmts)
        self.assertIn("image/jpeg,width=640,height=480,framerate=15/2", argv)


if __name__ == "__main__":
    unittest.main()
