# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""GStreamer plugin isolation – stand-in plugin files, GStreamer is never loaded."""

import os
import tempfile
import unittest
from unittest import mock

from avincecam import gstisolation, preview
from avincecam.model import Camera
from test_preview import FRONT, PreviewBase

ALL = ("libgstcoreelements.so", "libgstlibcamera.so", "libgstvideoconvertscale.so",
       "libgstautodetect.so", "libgstxvimagesink.so", "libgstximagesink.so",
       "libgstvideo4linux2.so", "libgstpipewire.so")


def fake_plugin_dir(path, names=ALL):
    os.makedirs(path, exist_ok=True)
    for name in names:
        open(os.path.join(path, name), "w").close()
    return path


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.system = fake_plugin_dir(os.path.join(self.tmp.name, "system"))
        self.private = os.path.join(self.tmp.name, "private")

    def tearDown(self):
        self.tmp.cleanup()

    def test_required_optional_and_one_of(self):
        linked = gstisolation.prepare_plugins(
            self.private, self.system, ("libgstlibcamera.so",),
            optional=("libgstnotthere.so",),
            need_one_of=("libgstwaylandsink.so", "libgstximagesink.so"))
        self.assertEqual(linked, ["libgstlibcamera.so", "libgstximagesink.so"])

    def test_no_video_output(self):
        with self.assertRaises(gstisolation.IsolationError) as ctx:
            gstisolation.prepare_plugins(self.private, self.system, ("libgstlibcamera.so",),
                                         need_one_of=("libgstwaylandsink.so",))
        self.assertEqual(ctx.exception.code, "plugin-missing")
        self.assertIn("plugins-base", ctx.exception.detail)

    def test_missing_libcamera_names_package(self):
        os.remove(os.path.join(self.system, "libgstlibcamera.so"))
        with self.assertRaises(gstisolation.IsolationError) as ctx:
            gstisolation.prepare_plugins(self.private, self.system, ("libgstlibcamera.so",))
        self.assertIn("gstreamer1.0-libcamera", ctx.exception.detail)

    def test_system_dir_override(self):
        with mock.patch.dict(os.environ, {gstisolation.SYSTEM_DIR_ENV: self.system}):
            self.assertEqual(gstisolation.find_plugin_dir(("/nonexistent",)), self.system)

    def test_isolated_environment(self):
        run = os.path.join(self.tmp.name, "run")
        os.makedirs(run)
        with mock.patch.dict(os.environ, {gstisolation.SYSTEM_DIR_ENV: self.system}):
            env = gstisolation.isolated_environment(run, "x", ("libgstlibcamera.so",))
        self.assertEqual(env["GST_PLUGIN_SYSTEM_PATH_1_0"], os.path.join(run, "x-plugins"))
        self.assertEqual(env["GST_REGISTRY_1_0"], os.path.join(run, "x-registry.bin"))
        self.assertEqual(os.listdir(os.path.join(run, "x-plugins")), ["libgstlibcamera.so"])


class ViewerEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.system = fake_plugin_dir(os.path.join(self.tmp.name, "system"))
        self.patch = mock.patch.dict(os.environ, {gstisolation.SYSTEM_DIR_ENV: self.system})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_gst_preview_of_libcamera_camera_is_isolated(self):
        cam = Camera(id=FRONT, label="Front camera", api="libcamera", backend_id="x")
        env = preview.viewer_environment(cam, "gst", self.tmp.name)
        linked = os.listdir(env["GST_PLUGIN_SYSTEM_PATH_1_0"])
        self.assertNotIn("libgstvideo4linux2.so", linked)
        self.assertNotIn("libgstpipewire.so", linked)
        self.assertIn("libgstautodetect.so", linked)
        self.assertIn("libgstxvimagesink.so", linked)

    def test_other_viewers_unchanged(self):
        cam = Camera(id=FRONT, label="Front camera", api="libcamera", backend_id="x")
        self.assertIsNone(preview.viewer_environment(cam, "qcam", self.tmp.name))
        uvc = Camera(id="usb:1-5", label="Webcam", api="v4l2", nodes=["/dev/video0"])
        self.assertIsNone(preview.viewer_environment(uvc, "gst", self.tmp.name))


class GstPreviewCliTest(PreviewBase):
    def test_viewer_runs_with_private_plugins(self):
        system = fake_plugin_dir(os.path.join(self.tmp.name, "gst-system"))
        env = dict(self.env, **{gstisolation.SYSTEM_DIR_ENV: system})
        proc, answer = self.start(FRONT, "--viewer", "gst", env=env)
        self.assertTrue(answer["started"], answer)
        self.wait_ready()
        self.core("stop")
        proc.wait(timeout=10)
        with open(os.path.join(self.run_dir, preview.LOG_NAME)) as log:
            text = log.read()
        self.assertIn("gst-preview-plugins", text)
        self.assertIn("libgstlibcamera.so", text)
        self.assertNotIn("libgstvideo4linux2.so", text)

    def test_missing_video_output_is_reported(self):
        system = fake_plugin_dir(os.path.join(self.tmp.name, "gst-system"),
                                 [n for n in ALL if "imagesink" not in n])
        env = dict(self.env, **{gstisolation.SYSTEM_DIR_ENV: system})
        proc, answer = self.start(FRONT, "--viewer", "gst", env=env)
        self.assertEqual(answer["errors"][0]["code"], "plugin-missing")
        self.assertEqual(proc.wait(timeout=10), 1)
        self.assertIsNone(preview.read_lock(self.run_dir))


if __name__ == "__main__":
    unittest.main()
